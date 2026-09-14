/**
 * Turning NODE B off **on purpose**, for the RULE 2 specs.
 *
 * ## Why this exists
 *
 * Two specs asserted `health.llm.reachable === false` as a *precondition* —
 * "the precondition this whole suite has been running under". That was true
 * for months only because the two machines were on different networks, so
 * NODE B was unreachable **by accident**. The moment the link was fixed on
 * 2026-09-14 both specs went red, having never once tested what their names
 * claim.
 *
 * A test that depends on ambient infrastructure is not testing RULE 2; it is
 * observing the weather. And it fails in the *wrong direction* — connecting
 * NODE B successfully made the suite red, which is exactly backwards.
 *
 * So: **make the condition, do not wait for it.** The admin kill switch is the
 * product's own designed way to turn inference off (Phase 5, "an admin must be
 * able to make that trade in one click at 3am"), so the spec uses the same
 * lever a hospital would. This now proves RULE 2 whether NODE B is powered on,
 * powered off, or has never existed.
 */

import { expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

import { apiAuth } from "./auth";

/** Shape of the bit of `/api/health` these specs care about. */
export type Health = {
  status: string;
  db: string;
  llm: { reachable: boolean; error?: string };
  degraded_features: string[];
};

async function setKillSwitch(
  request: APIRequestContext,
  adminCode: string,
  enabled: boolean,
  reason: string,
): Promise<void> {
  const headers = await apiAuth(request, adminCode);
  const response = await request.post("/api/admin/node-b/kill-switch", {
    headers,
    data: { llm_enabled: enabled, reason },
  });
  expect(
    response.status(),
    `kill switch -> ${enabled} failed: ${await response.text()}`,
  ).toBe(200);
}

/**
 * Poll `/api/health` until `llm.reachable` settles on `want`.
 *
 * The endpoint documents the switch as taking effect "within 10 seconds", and
 * the probe caches its result, so the change is **not** visible on the next
 * request. Polling is the honest way to wait for a documented delay — as
 * opposed to a fixed sleep, which CLAUDE.md bans because it hides races rather
 * than resolving them.
 */
export async function waitForLlm(
  request: APIRequestContext,
  want: boolean,
  timeoutMs = 30_000,
): Promise<Health> {
  const deadline = Date.now() + timeoutMs;
  let last: Health | undefined;
  while (Date.now() < deadline) {
    last = (await (await request.get("/api/health")).json()) as Health;
    if (last.llm.reachable === want) return last;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(
    `/api/health never reported llm.reachable=${want} within ${timeoutMs}ms. ` +
      `Last seen: ${JSON.stringify(last)}`,
  );
}

/**
 * Run `body` with NODE B inference switched **off**, then switch it back on.
 *
 * The restore runs in `finally`, so a failing assertion inside `body` cannot
 * leave the stack degraded for every spec that follows — a leaked kill switch
 * would turn one red test into a red suite and send the next person hunting a
 * bug that does not exist.
 */
export async function withNodeBDisabled<T>(
  request: APIRequestContext,
  adminCode: string,
  body: (health: Health) => Promise<T>,
): Promise<T> {
  await setKillSwitch(request, adminCode, false, "E2E: proving RULE 2 holds");
  try {
    const health = await waitForLlm(request, false);

    // The guarantee itself: unreachable is a *degraded* state, never an outage.
    expect(health.status).toBe("ok");
    expect(health.db).toBe("ok");
    expect(health.degraded_features).toEqual(["llm_generation"]);

    return await body(health);
  } finally {
    await setKillSwitch(request, adminCode, true, "E2E: restoring after RULE 2");
    // Best-effort restore. Do not fail the spec here -- if NODE B is genuinely
    // powered off, `reachable` will never come back true and that is not this
    // test's business.
    await waitForLlm(request, true, 15_000).catch(() => undefined);
  }
}
