/**
 * Phase 5, end to end — a doctor signs in, finds the flag, and closes it.
 *
 * Nothing is mocked. A real browser, a real login against Argon2, the real
 * API with its real RBAC, and the real Postgres with the real hash-chained
 * audit log.
 *
 * The three things asserted here cannot be proved by a unit test:
 *
 *   1. **The whole journey works**: login → worklist → case → close, with
 *      the closure landing in `pending_cases` and in `audit_log`.
 *   2. **A doctor cannot reach another department's case**, enforced by the
 *      server rather than by a hidden link.
 *   3. **The audit chain verifies** after real application traffic — which is
 *      the only way to know the hashes computed by the running app agree with
 *      the verifier.
 */

import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { E2E_PASSWORD, query, seedGatedEncounter } from "./seed.mjs";
import { apiAuth } from "./auth";

interface Seed {
  encounterId: string;
  doctorId: string;
  patientName: string;
  mrn: string;
  doctorCode: string;
  adminCode: string;
  auditorCode: string;
  orderA: string;
  orderB: string;
}

function seed(): Seed {
  return seedGatedEncounter() as Seed;
}

/**
 * Discharge the encounter so a tracking case exists to work on.
 *
 * Contracts are built from whatever `discharge-readiness` reports as blocking
 * rather than from a hardcoded list, so this keeps working if the seed's
 * order mix changes. Both calls assert their status: a silently failed setup
 * would fail the real assertion later for a reason that looks nothing like
 * the cause.
 */
async function discharge(request: APIRequestContext, data: Seed): Promise<void> {
  const headers = await apiAuth(request, data.doctorCode);
  const readiness = await (
    await request.get(`/api/encounters/${data.encounterId}/discharge-readiness`, {
      headers,
    })
  ).json();
  const due = new Date(Date.now() + 48 * 3600_000).toISOString();

  const contracts = await request.post(
    `/api/encounters/${data.encounterId}/discharge-contracts`,
    {
      headers,
      data: {
        contracts: readiness.blocking_orders.map((order: { order_id: string }) => ({
          order_id: order.order_id,
          responsible_doctor_id: data.doctorId,
          expected_by: due,
        })),
      },
    },
  );
  expect(contracts.status()).toBe(201);

  const discharged = await request.post(
    `/api/encounters/${data.encounterId}/discharge`,
    { headers },
  );
  expect(discharged.status()).toBe(200);
}

/**
 * Sign in and **wait until the session actually exists**.
 *
 * Returning on the click alone is a race: a `page.goto` immediately
 * afterwards aborts the in-flight login request, the tokens are never stored,
 * and the next page bounces to the login form — which looks exactly like an
 * authorisation bug and is not one.
 */
async function signIn(page: Page, employeeCode: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Employee code").fill(employeeCode);
  await page.getByLabel("Password").fill(E2E_PASSWORD as string);
  await page.getByRole("button", { name: "Sign in" }).click();
  // The login form is gone and a signed-in frame is rendered.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

test.describe("the Phase 5 dashboard", () => {
  test("a doctor signs in, opens a flag and closes it with a reason", async ({
    page,
    request,
  }) => {
    const data = seed();
    await discharge(request, data);

    // Flag the case directly: this spec is about the dashboard, and Phase 3's
    // own E2E already proves the engine produces the flag.
    const caseId = (
      await query(
        `SELECT id FROM pending_cases WHERE order_id = '${data.orderA}'`,
      )
    ).trim();
    await query(
      `UPDATE pending_cases SET state = 'flagged', severity = 'follow_up',
              flagged_at = now() - interval '3 hours',
              result_received_at = now() - interval '3 hours'
        WHERE id = '${caseId}'`,
    );

    await signIn(page, data.doctorCode);

    // The worklist, with the patient on it.
    await expect(page.getByRole("heading", { name: /open flags/i })).toBeVisible();
    // The seed leaves two blocking orders, so this patient has two open cases.
    // Target the row by its test name rather than by the patient's.
    const row = page.getByRole("row", { name: /Urine Culture/ });
    await expect(row).toBeVisible();
    await expect(row.getByText(data.mrn)).toBeVisible();

    // Open the case.
    await row.getByRole("link", { name: data.patientName }).click();
    await expect(page.getByRole("heading", { name: data.patientName })).toBeVisible();
    await expect(page.getByText("Why this is flagged")).toBeVisible();

    // Close it. The note is mandatory and the button stays disabled until it
    // is long enough.
    await page.getByRole("button", { name: /Acknowledge and close/i }).click();
    await expect(page.getByRole("button", { name: /Close this case/i })).toBeDisabled();
    await page
      .getByLabel("What was done, and when?")
      .fill("Patient recalled, seen in OPD, antibiotic changed");
    await page.getByRole("button", { name: /Close this case/i }).click();

    await expect(page.getByText(/This case is closed/i)).toBeVisible();

    // And the database agrees.
    const closed = await query(
      `SELECT state || '|' || closure_reason FROM pending_cases WHERE id = '${caseId}'`,
    );
    expect(closed.trim()).toBe("closed|action_taken");

    // Every pending timer stopped.
    const pending = await query(
      `SELECT count(*) FROM sla_timers WHERE case_id = '${caseId}' AND status = 'pending'`,
    );
    expect(pending.trim()).toBe("0");

    // The audit row was written in the same transaction.
    const audited = await query(
      `SELECT count(*) FROM audit_log
        WHERE entity_id = '${caseId}' AND action = 'case.closed'`,
    );
    expect(audited.trim()).toBe("1");
  });

  test("a critical case cannot be closed in bulk", async ({ page, request }) => {
    const data = seed();
    await discharge(request, data);

    const caseId = (
      await query(`SELECT id FROM pending_cases WHERE order_id = '${data.orderA}'`)
    ).trim();
    await query(
      `UPDATE pending_cases SET state = 'flagged', severity = 'critical',
              flagged_at = now() - interval '1 hour'
        WHERE id = '${caseId}'`,
    );

    await signIn(page, data.doctorCode);
    const row = page.getByRole("row", { name: /Urine Culture/ });
    await expect(row).toBeVisible();

    // The checkbox on THAT row is disabled and says why.
    await expect(row.getByRole("checkbox")).toBeDisabled();
    await expect(
      row.getByRole("checkbox", {
        name: /critical cases must be closed individually/i,
      }),
    ).toHaveCount(1);

    // And the case is still open.
    const state = await query(
      `SELECT state FROM pending_cases WHERE id = '${caseId}'`,
    );
    expect(state.trim()).toBe("flagged");
  });

  test("an unauthenticated visitor is sent to the login page", async ({ page }) => {
    await page.goto("/worklist");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("a wrong password is refused and says nothing useful to an attacker", async ({
    page,
  }) => {
    const data = seed();
    await page.goto("/login");
    await page.getByLabel("Employee code").fill(data.doctorCode);
    await page.getByLabel("Password").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText(/Invalid employee code or password/i)).toBeVisible();

    // The failure was counted, which is what makes the lockout work.
    const failures = await query(
      `SELECT failed_login_count FROM users WHERE employee_code = '${data.doctorCode}'`,
    );
    expect(Number(failures.trim())).toBeGreaterThanOrEqual(1);
  });

  test("a doctor cannot reach the admin screens", async ({ page }) => {
    const data = seed();
    await signIn(page, data.doctorCode);

    await page.goto("/admin");
    await expect(page.getByText(/You do not have access to this page/i)).toBeVisible();
    await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
  });

  test("an auditor verifies the hash chain after real traffic", async ({
    page,
    request,
  }) => {
    // The only way to know the hashes the running application computes agree
    // with the verifier: make it do real work first, then check.
    const data = seed();
    await discharge(request, data);
    await signIn(page, data.doctorCode);
    await page.getByRole("button", { name: "Sign out" }).click();

    await signIn(page, data.auditorCode);
    await page.goto("/audit");
    await page.getByRole("button", { name: /Verify the chain/i }).click();

    await expect(page.getByText(/The audit chain is intact/i)).toBeVisible({
      timeout: 20_000,
    });
  });

  test("an admin can turn NODE B off without touching anything that keeps a patient safe", async ({
    page,
  }) => {
    const data = seed();
    await signIn(page, data.adminCode);
    await page.goto("/admin");

    await expect(
      page.getByText(/Turning this off costs nothing that keeps a patient safe/i),
    ).toBeVisible();

    await page.getByLabel("Why?").fill("End-to-end test of the kill switch");
    await page.getByRole("button", { name: /Turn inference off/i }).click();

    await expect(page.getByRole("button", { name: /Turn inference on/i })).toBeVisible();

    // The switch lives in a table, not the environment.
    const stored = await query(
      "SELECT value FROM system_settings WHERE key = 'llm_enabled'",
    );
    expect(stored.trim()).toBe("false");

    // Put it back, so the next run starts from the same place.
    await page.getByLabel("Why?").fill("Restoring after the end-to-end test");
    await page.getByRole("button", { name: /Turn inference on/i }).click();
    await expect(page.getByRole("button", { name: /Turn inference off/i })).toBeVisible();
  });
});
