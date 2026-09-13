/**
 * Signing in, for the E2E specs. Phase 5.1.
 *
 * Phase 5 put every screen behind a login, so the Phase 1–3 specs — which
 * used to navigate straight to a URL — now have to authenticate first. That
 * is not an accident of the tests: it is the product change, and the specs
 * exercising it is the point.
 *
 * `storageState` would be faster, but it would also skip the login every one
 * of these journeys now genuinely starts with. Eight seconds of real logins
 * across the suite is the right price for not mocking the thing Phase 5
 * added.
 */

import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { E2E_PASSWORD } from "./seed.mjs";

/**
 * Sign in and **wait until the session actually exists**.
 *
 * Returning on the click alone is a race: a `page.goto` immediately
 * afterwards aborts the in-flight login request, the tokens are never
 * stored, and the next page bounces back to the login form — which looks
 * exactly like an authorisation bug and is not one.
 *
 * **Idempotent on purpose.** Specs call this before each navigation rather
 * than tracking whether they have already signed in, and a second call must
 * not navigate away from the page the test is halfway through. If a signed-in
 * frame is already rendered, this does nothing.
 */
export async function signIn(page: Page, employeeCode: string): Promise<void> {
  if ((await page.getByRole("button", { name: "Sign out" }).count()) > 0) return;

  await page.goto("/login");
  await page.getByLabel("Employee code").fill(employeeCode);
  await page.getByLabel("Password").fill(E2E_PASSWORD as string);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/** Sign in as the seeded attending doctor for this run. */
export async function signInAsDoctor(
  page: Page,
  data: { doctorCode: string },
): Promise<void> {
  await signIn(page, data.doctorCode);
}

/**
 * A bearer header for calls made straight to the API.
 *
 * Several specs deliberately bypass the browser to prove *the server* refuses
 * something — "a stale page cannot talk the server into a discharge". Those
 * calls carry no token of their own now that Phase 5.1 put RBAC on every
 * endpoint, so they need one explicitly.
 *
 * Note what this is **not** for: it must never be used to make an assertion
 * about authorisation pass. The specs that test authorisation use no header
 * at all, and expect a 401.
 */
export async function apiAuth(
  request: import("@playwright/test").APIRequestContext,
  employeeCode: string,
): Promise<Record<string, string>> {
  const response = await request.post("/api/auth/login", {
    data: { employee_code: employeeCode, password: E2E_PASSWORD as string },
  });
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { access_token: string };
  return { Authorization: `Bearer ${body.access_token}` };
}
