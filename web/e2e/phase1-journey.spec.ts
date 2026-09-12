/**
 * The Phase 1 journey and its safety invariants, end to end.
 *
 * `discharge-gate.spec.ts` covers the gate screen itself. This covers the road
 * to it — the Phase 1.5 supporting screens — and the invariants that only mean
 * anything when a real browser, a real API and a real database are all in play:
 * that a stale page cannot talk the server into a discharge, that pressing the
 * button twice does not open two sets of cases, and that an order created at
 * the wrong moment cannot slip past the gate.
 *
 * Nothing here is mocked. Every assertion is either something a clinician can
 * see on screen or a row read back out of Postgres.
 */

import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { query, seedGatedEncounter } from "./seed.mjs";

interface Seed {
  encounterId: string;
  doctorId: string;
  unitHeadId: string;
  attendingName: string;
  otherName: string;
  unitHeadName: string;
  patientName: string;
  mrn: string;
  orderA: string;
  orderB: string;
  /** Already resulted (`final`). Must never block. */
  orderC: string;
}

function seed(): Seed {
  return seedGatedEncounter() as Seed;
}

function inHours(hours: number): string {
  const at = new Date(Date.now() + hours * 3600_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(
    at.getHours(),
  )}:${pad(at.getMinutes())}`;
}

/** Drive the gate through to a completed discharge, entirely through the UI. */
async function dischargeThroughTheUi(page: import("@playwright/test").Page, data: Seed) {
  await page.goto(`/encounters/${data.encounterId}/discharge`);
  await page.getByRole("button", { name: "Assign responsibility" }).click();
  await page.locator(`#expected-${data.orderA}`).fill(inHours(30));
  await page.locator(`#expected-${data.orderB}`).fill(inHours(30));
  await page.getByRole("button", { name: "Review" }).click();
  await page.getByRole("button", { name: "Confirm discharge" }).click();
  await expect(page.getByText(`${data.patientName} discharged`)).toBeVisible();
}

/** Give every blocking order an owner, via the real API. */
async function contractEverything(
  request: APIRequestContext,
  data: Seed,
): Promise<void> {
  const readiness = await (
    await request.get(`/api/encounters/${data.encounterId}/discharge-readiness`)
  ).json();
  const due = new Date(Date.now() + 48 * 3600_000).toISOString();
  const response = await request.post(
    `/api/encounters/${data.encounterId}/discharge-contracts`,
    {
      data: {
        contracts: readiness.blocking_orders.map((order: { order_id: string }) => ({
          order_id: order.order_id,
          responsible_doctor_id: data.doctorId,
          expected_by: due,
        })),
      },
    },
  );
  expect(response.status()).toBe(201);
}

// ── the road to the gate: Phase 1.5 screens ───────────────────────────

test.describe("a clinician's journey to the gate", () => {
  test("find the patient, open the encounter, reach the gate", async ({ page }) => {
    const data = seed();

    // 1. Find the patient by name.
    await page.goto("/patients");
    await page.getByLabel(/Search by MRN/).fill(data.patientName);
    const hit = page.getByRole("link", { name: new RegExp(data.mrn) });
    await expect(hit).toBeVisible();

    // 2. Open the patient, then the encounter.
    await hit.click();
    await expect(page.getByRole("heading", { name: data.patientName })).toBeVisible();
    await page.getByRole("link", { name: /E2E-ENC-/ }).click();

    // 3. The outstanding investigations are visible, and the gate is blocked.
    await expect(page.getByText("Urine Culture", { exact: true })).toBeVisible();
    await expect(page.getByText("HbA1c", { exact: true })).toBeVisible();
    await expect(
      page.getByText(/Discharge blocked — 2 investigations have no one responsible/),
    ).toBeVisible();

    // 4. And it links to the gate.
    await page.getByRole("link", { name: "Open discharge gate" }).click();
    await expect(page.getByRole("heading", { name: /Step 1/ })).toBeVisible();
  });

  test("finds the patient by MRN as well as by name", async ({ page }) => {
    const data = seed();
    await page.goto("/patients");
    await page.getByLabel(/Search by MRN/).fill(data.mrn);
    await expect(page.getByRole("link", { name: new RegExp(data.mrn) })).toBeVisible();
  });

  test("a manual order immediately changes the gate's answer", async ({ page }) => {
    const data = seed();
    await contractEverything(page.request, data);

    await page.goto(`/encounters/${data.encounterId}`);
    await expect(page.getByText("Ready to discharge")).toBeVisible();

    // Add one through the UI.
    await page.getByRole("button", { name: "Add investigation" }).click();
    await page.getByLabel("Test code").fill("CRP");
    await page.getByLabel("Test name").fill("C-Reactive Protein");
    await page.getByLabel(/Turnaround time/).fill("6");
    await page.getByRole("button", { name: "Add investigation" }).click();

    // Readiness updates without a reload, and the screen says so.
    await expect(
      page.getByText(/Discharge blocked — 1 investigation has no one responsible/),
    ).toBeVisible();

    expect(
      query(
        `SELECT can_discharge FROM (SELECT count(*) = 0 AS can_discharge FROM orders o ` +
          `LEFT JOIN discharge_contracts c ON c.order_id = o.id AND c.deleted_at IS NULL ` +
          `WHERE o.encounter_id = '${data.encounterId}' AND o.deleted_at IS NULL ` +
          `AND o.status NOT IN ('final','cancelled','rejected') AND c.id IS NULL) x`,
      ),
    ).toBe("f");
  });
});

// ── what a completed discharge leaves behind ──────────────────────────

test.describe("the safety state a discharge creates", () => {
  test("opens a case per order, owned by the contracted doctor, with an event and a timer", async ({
    page,
  }) => {
    const data = seed();
    await dischargeThroughTheUi(page, data);

    // A pending case per outstanding order.
    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");

    // Owned by the doctor the contract names — not by whoever clicked.
    expect(
      query(
        `SELECT count(*) FROM pending_cases pc ` +
          `JOIN discharge_contracts c ON c.id = pc.contract_id ` +
          `WHERE pc.encounter_id = '${data.encounterId}' ` +
          `AND pc.current_owner_id = c.responsible_doctor_id`,
      ),
    ).toBe("2");

    // An append-only case_event per case.
    expect(
      query(
        `SELECT count(*) FROM case_events ce ` +
          `JOIN pending_cases pc ON pc.id = ce.case_id ` +
          `WHERE pc.encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
    expect(
      query(
        `SELECT DISTINCT ce.event_type FROM case_events ce ` +
          `JOIN pending_cases pc ON pc.id = ce.case_id ` +
          `WHERE pc.encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("case_opened");

    // And an SLA timer queued in the same transaction — Phase 2 consumes it.
    expect(
      query(
        `SELECT count(*) FROM pgmq.q_sla_timers ` +
          `WHERE message->>'encounter_id' = '${data.encounterId}'`,
      ),
    ).toBe("2");
  });

  test("the case_events trigger still refuses to be rewritten", async ({ page }) => {
    const data = seed();
    await dischargeThroughTheUi(page, data);

    // The guarantee, asserted against the live database rather than assumed.
    //
    // The outcome is written to a temp table and selected back, NOT raised as
    // a NOTICE: psql sends notices to stderr, and this helper reads stdout, so
    // a NOTICE-based check reports the command tag "DO" and passes whatever
    // the trigger actually did. This project has been caught by that once
    // already, in the Phase 1 audit.
    const outcome = query(
      `CREATE TEMP TABLE _guard(v text); ` +
        `DO $$ BEGIN ` +
        `UPDATE case_events SET event_type = 'tampered' WHERE case_id IN ` +
        `(SELECT id FROM pending_cases WHERE encounter_id = '${data.encounterId}'); ` +
        `INSERT INTO _guard VALUES ('UPDATE_WAS_ALLOWED'); ` +
        `EXCEPTION WHEN restrict_violation THEN ` +
        `INSERT INTO _guard VALUES ('REFUSED'); END $$; ` +
        `SELECT v FROM _guard;`,
    );
    expect(outcome).toContain("REFUSED");
    expect(outcome).not.toContain("UPDATE_WAS_ALLOWED");

    // The rows are untouched.
    expect(
      query(
        `SELECT count(*) FROM case_events ce ` +
          `JOIN pending_cases pc ON pc.id = ce.case_id ` +
          `WHERE pc.encounter_id = '${data.encounterId}' ` +
          `AND ce.event_type = 'case_opened'`,
      ),
    ).toBe("2");
  });
});

// ── the invariants a browser can actually threaten ────────────────────

test.describe("the backend does not trust the browser", () => {
  test("a stale page cannot talk the server into a discharge", async ({ page }) => {
    const data = seed();
    await contractEverything(page.request, data);

    // The doctor opens the gate while everything is owned. The page now holds
    // a readiness that says "ready".
    await page.goto(`/encounters/${data.encounterId}/discharge`);
    await expect(
      page.getByText("Every outstanding investigation has an owner"),
    ).toBeVisible();

    // Meanwhile, somewhere else, a new investigation is ordered.
    const added = await page.request.post(
      `/api/encounters/${data.encounterId}/orders`,
      {
        data: {
          test_code: "STALE",
          test_name: "Ordered While The Page Was Open",
          category: "lab",
        },
      },
    );
    expect(added.status()).toBe(201);

    // The doctor, looking at a page that still says "ready", confirms.
    await page.getByRole("button", { name: "Review and discharge" }).click();
    await page.getByRole("button", { name: "Confirm discharge" }).click();

    // The server re-derives readiness under its row lock and refuses.
    await expect(
      page.getByText(/Discharge blocked by outstanding investigations/),
    ).toBeVisible();
    expect(
      query(`SELECT status FROM encounters WHERE id = '${data.encounterId}'`),
    ).toBe("active");
  });

  test("discharging twice does not open a second set of cases", async ({ page }) => {
    const data = seed();
    await dischargeThroughTheUi(page, data);

    const casesAfterFirst = query(
      `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
    );

    // Same request the button makes, replayed.
    const replay = await page.request.post(
      `/api/encounters/${data.encounterId}/discharge`,
    );
    expect(replay.status()).toBe(409);

    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe(casesAfterFirst);
    expect(
      query(
        `SELECT count(*) FROM case_events ce JOIN pending_cases pc ON pc.id = ce.case_id ` +
          `WHERE pc.encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
  });

  test("a raw API discharge with no contracts is refused", async ({ page }) => {
    // Exit Gate 1, second clause: the gate is not a UI convention.
    const data = seed();
    const response = await page.request.post(
      `/api/encounters/${data.encounterId}/discharge`,
    );
    expect(response.status()).toBe(409);

    const body = await response.json();
    expect(body.title).toContain("blocked");
    expect(body.blocking_orders).toHaveLength(2);

    expect(
      query(`SELECT status FROM encounters WHERE id = '${data.encounterId}'`),
    ).toBe("active");
    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("0");
  });

  test("an order created at the same instant as a discharge cannot slip past", async ({
    page,
  }) => {
    const data = seed();
    await contractEverything(page.request, data);

    // Fired together, over real HTTP, against the real stack.
    const [order, discharge] = await Promise.all([
      page.request.post(`/api/encounters/${data.encounterId}/orders`, {
        data: { test_code: "RACE", test_name: "Race Order", category: "lab" },
      }),
      page.request.post(`/api/encounters/${data.encounterId}/discharge`),
    ]);

    // Either ordering is legitimate; both succeeding is not.
    const outcome = [order.status(), discharge.status()].join("/");
    expect(["201/409", "409/200"]).toContain(outcome);

    // The invariant, read out of the database: if it is discharged, nothing
    // outstanding is unowned.
    const status = query(
      `SELECT status FROM encounters WHERE id = '${data.encounterId}'`,
    );
    const unowned = query(
      `SELECT count(*) FROM orders o ` +
        `LEFT JOIN discharge_contracts c ON c.order_id = o.id AND c.deleted_at IS NULL ` +
        `WHERE o.encounter_id = '${data.encounterId}' AND o.deleted_at IS NULL ` +
        `AND o.status NOT IN ('final','cancelled','rejected') AND c.id IS NULL`,
    );
    if (status === "discharged") {
      expect(unowned).toBe("0");
    }
  });

  test("an order cannot be added once the encounter is discharged", async ({ page }) => {
    const data = seed();
    await dischargeThroughTheUi(page, data);

    const late = await page.request.post(`/api/encounters/${data.encounterId}/orders`, {
      data: { test_code: "LATE", test_name: "Late Order", category: "lab" },
    });
    expect(late.status()).toBe(409);

    // And the screen agrees: no button to offer it.
    await page.goto(`/encounters/${data.encounterId}`);
    await expect(page.getByRole("button", { name: "Add investigation" })).toHaveCount(0);
  });
});

// ── RULE 2 ────────────────────────────────────────────────────────────

test.describe("NODE B is not required", () => {
  test("the whole Phase 1 flow completes with the LLM unreachable", async ({ page }) => {
    const health = await (await page.request.get("/api/health")).json();

    // The precondition this whole suite has been running under.
    expect(health.status).toBe("ok");
    expect(health.db).toBe("ok");
    expect(health.llm.reachable).toBe(false);
    expect(health.degraded_features).toEqual(["llm_generation"]);

    // And with it unreachable, a discharge still completes and still tracks.
    const data = seed();
    await dischargeThroughTheUi(page, data);
    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
  });
});

// ── Exit Gate 1, in its own words ─────────────────────────────────────

test.describe("Exit Gate 1", () => {
  /**
   * *"admit patient → order 3 tests → 1 resulted, 2 pending → click discharge
   * → blocked → assign owners and dates → discharge succeeds → 2 pending cases
   * exist with timers queued"*
   *
   * Run as one uninterrupted browser journey, asserted at each step, against
   * the real stack. The resulted test is the control: if the gate blocked on
   * it too, "blocked" would mean nothing.
   */
  test("the demo the build plan asks for, start to finish", async ({ page }) => {
    const data = seed();

    // 3 tests ordered: 1 resulted, 2 pending.
    await page.goto(`/encounters/${data.encounterId}`);
    await expect(page.getByText("Investigations (3)")).toBeVisible();
    await expect(page.getByText("Outstanding (2)")).toBeVisible();
    await expect(page.getByText("Resulted or closed (1)")).toBeVisible();

    // Click discharge → blocked. The resulted test is not among the blockers.
    await page.getByRole("link", { name: "Open discharge gate" }).click();
    await expect(
      page.getByText("2 investigations have no one responsible for their results"),
    ).toBeVisible();
    const step1 = page.getByRole("region", { name: /Step 1/ });
    await expect(step1).toContainText("Urine Culture");
    await expect(step1).toContainText("HbA1c");
    await expect(step1).not.toContainText("Chest X-Ray");

    // Assign owners and dates.
    await page.getByRole("button", { name: "Assign responsibility" }).click();
    await page.locator(`#expected-${data.orderA}`).fill(inHours(30));
    await page.locator(`#expected-${data.orderB}`).fill(inHours(30));
    await page.getByRole("button", { name: "Review" }).click();

    // Discharge succeeds.
    await page.getByRole("button", { name: "Confirm discharge" }).click();
    await expect(page.getByText(`${data.patientName} discharged`)).toBeVisible();
    expect(
      query(`SELECT status FROM encounters WHERE id = '${data.encounterId}'`),
    ).toBe("discharged");

    // Exactly 2 pending cases — the resulted test opened none.
    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
    expect(
      query(
        `SELECT count(*) FROM pending_cases pc JOIN discharge_contracts c ` +
          `ON c.id = pc.contract_id WHERE c.order_id = '${data.orderC}'`,
      ),
    ).toBe("0");

    // With timers queued.
    expect(
      query(
        `SELECT count(*) FROM pgmq.q_sla_timers ` +
          `WHERE message->>'encounter_id' = '${data.encounterId}'`,
      ),
    ).toBe("2");
  });
});
