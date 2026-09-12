/**
 * Phase 3, end to end — a lab tech types a result and the case moves.
 *
 * Nothing is mocked. A real browser, the real API, the real Postgres with its
 * real rule tables, and the real worker draining the real `classify` queue.
 *
 * **Exit Gate 3 names three cases by hand.** All three are here, each asserted
 * both on screen (what the tech is told before saving) and in the database
 * (what the engine actually recorded):
 *
 *   1. a culture resistant to a discharge antibiotic produces CRITICAL;
 *   2. *"no evidence of malignancy"* does not produce a flag;
 *   3. a contaminant culture produces FOLLOW_UP and does not auto-close.
 *
 * The fourth gate clause — ≥95% agreement with a clinician — cannot be
 * demonstrated by any test. See docs/clinical-validation.md.
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { query, seedGatedEncounter } from "./seed.mjs";

interface Seed {
  encounterId: string;
  doctorId: string;
  patientName: string;
  orderA: string;
  orderB: string;
  orderC: string;
}

function seed(): Seed {
  return seedGatedEncounter() as Seed;
}

/** Discharge the encounter so there is a tracking case to move. */
async function discharge(
  request: import("@playwright/test").APIRequestContext,
  data: Seed,
): Promise<void> {
  const readiness = await (
    await request.get(`/api/encounters/${data.encounterId}/discharge-readiness`)
  ).json();
  const due = new Date(Date.now() + 48 * 3600_000).toISOString();
  const contracts = await request.post(
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
  expect(contracts.status()).toBe(201);
  const discharged = await request.post(
    `/api/encounters/${data.encounterId}/discharge`,
  );
  expect(discharged.status()).toBe(200);
}

/** Put the patient on an antibiotic, through the real endpoint. */
async function prescribe(
  request: import("@playwright/test").APIRequestContext,
  data: Seed,
  drugName: string,
): Promise<void> {
  const response = await request.post(
    `/api/encounters/${data.encounterId}/discharge-medications`,
    { data: { drug_name: drugName, is_antibiotic: true } },
  );
  expect(response.status()).toBe(201);
}

/** Wait for the worker to drain the classify queue for this result. */
async function classified(resultId: string): Promise<string> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const severity = query(
      `SELECT severity FROM classifications WHERE result_id = '${resultId}'`,
    );
    if (severity) return severity;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`the worker never classified ${resultId}`);
}

/** Read back the result id the API assigned to this order's newest report. */
function newestResultFor(orderId: string): string {
  return query(
    `SELECT id FROM results WHERE order_id = '${orderId}'
      ORDER BY received_at DESC LIMIT 1`,
  );
}

async function openEntry(page: Page, data: Seed, orderId: string): Promise<void> {
  await page.goto(`/encounters/${data.encounterId}/orders/${orderId}/result`);
  await expect(page.getByRole("heading", { name: /enter a result/i })).toBeVisible();
}

// ── 1. the case the product exists for ────────────────────────────────

test.describe("a culture resistant to a discharge antibiotic", () => {
  test("is predicted critical before saving and recorded critical after", async ({
    page,
    request,
  }) => {
    const data = seed();
    await prescribe(request, data, "Monocef");
    await discharge(request, data);

    await openEntry(page, data, data.orderA);

    await page.getByRole("button", { name: "Add an organism" }).click();
    await page.getByLabel("Organism", { exact: true }).fill("Escherichia coli");
    await page.getByLabel("Colony count").fill(">100,000 CFU/mL");
    await page.getByLabel("Specimen").fill("urine");
    await page
      .getByLabel("Antibiotic 1 for organism 1")
      .fill("Ceftriaxone");
    await page.getByRole("radio", { name: "R for Ceftriaxone" }).check();

    // ── what the tech is told, before anything is saved ──────────
    const preview = page.getByRole("complementary", { name: /predicted severity/i });
    await expect(preview.getByText("Critical", { exact: true })).toBeVisible();
    await expect(
      preview.getByText(/resistant to a discharge antibiotic/i),
    ).toBeVisible();
    await expect(preview.getByText(/Monocef/).first()).toBeVisible();

    // Nothing has been written yet. The preview is a prediction, not a save.
    expect(
      query(`SELECT count(*) FROM results WHERE order_id = '${data.orderA}'`),
    ).toBe("0");

    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    // ── what the engine actually decided ─────────────────────────
    const resultId = newestResultFor(data.orderA);
    expect(await classified(resultId)).toBe("critical");

    const caseRow = query(
      `SELECT state || '|' || severity FROM pending_cases
        WHERE order_id = '${data.orderA}'`,
    );
    expect(caseRow).toBe("flagged|critical");

    // The explanation is stored with it, so the decision can be re-read in
    // six months against the engine version that made it.
    const explanation = query(
      `SELECT rule_outputs->0->>'reason_code' FROM classifications
        WHERE result_id = '${resultId}'`,
    );
    expect(explanation).toBe("CULT_RESISTANT_TO_DISCHARGE_DRUG");
    expect(
      query(
        `SELECT engine_version FROM classifications WHERE result_id = '${resultId}'`,
      ),
    ).not.toBe("");
  });

  test("the same organism susceptible to the same drug closes the case", async ({
    page,
    request,
  }) => {
    const data = seed();
    await prescribe(request, data, "Monocef");
    await discharge(request, data);
    await openEntry(page, data, data.orderA);

    await page.getByRole("button", { name: "Add an organism" }).click();
    await page.getByLabel("Organism", { exact: true }).fill("Escherichia coli");
    await page.getByLabel("Colony count").fill(">100,000 CFU/mL");
    await page.getByLabel("Specimen").fill("urine");
    await page.getByLabel("Antibiotic 1 for organism 1").fill("Ceftriaxone");
    await page.getByRole("radio", { name: "S for Ceftriaxone" }).check();

    const preview = page.getByRole("complementary", { name: /predicted severity/i });
    await expect(preview.getByText("Normal", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    const resultId = newestResultFor(data.orderA);
    expect(await classified(resultId)).toBe("normal");

    await expect(async () => {
      expect(
        query(
          `SELECT state FROM pending_cases WHERE order_id = '${data.orderA}'`,
        ),
      ).toBe("closed");
    }).toPass({ timeout: 10_000 });
  });
});

// ── 2. the negation case ──────────────────────────────────────────────

test.describe('"no evidence of malignancy"', () => {
  test("does not produce a flag", async ({ page, request }) => {
    const data = seed();
    await discharge(request, data);

    // orderB is the lab order; a radiology report is the honest home for this
    // sentence, and orderC is already final. Use orderB and give it prose.
    await openEntry(page, data, data.orderB);

    await page.getByRole("button", { name: "Add a section" }).click();
    await page.getByLabel("Text", { exact: true }).fill("No evidence of malignancy.");

    const preview = page.getByRole("complementary", { name: /predicted severity/i });
    await expect(preview.getByText(/Critical/)).toHaveCount(0);
    await expect(preview.getByText(/every finding named was negated/i)).toBeVisible();

    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    const resultId = newestResultFor(data.orderB);
    const severity = await classified(resultId);
    expect(severity).not.toBe("critical");

    // And the unnegated sentence is what it should have been compared to.
    const caseState = query(
      `SELECT state FROM pending_cases WHERE order_id = '${data.orderB}'`,
    );
    expect(caseState).not.toBe("closed");
  });

  test("the same finding unnegated does produce a flag", async ({
    page,
    request,
  }) => {
    const data = seed();
    await discharge(request, data);
    await openEntry(page, data, data.orderB);

    await page.getByRole("button", { name: "Add a section" }).click();
    await page
      .getByLabel("Text", { exact: true })
      .fill("Findings are consistent with malignancy.");

    const preview = page.getByRole("complementary", { name: /predicted severity/i });
    await expect(preview.getByText("Critical", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    expect(await classified(newestResultFor(data.orderB))).toBe("critical");
  });
});

// ── 3. the contaminant case ───────────────────────────────────────────

test.describe("a contaminant culture", () => {
  test("is FOLLOW_UP, not CRITICAL, and does not auto-close", async ({
    page,
    request,
  }) => {
    const data = seed();
    await prescribe(request, data, "Augmentin");
    await discharge(request, data);
    await openEntry(page, data, data.orderA);

    await page.getByRole("button", { name: "Add an organism" }).click();
    await page
      .getByLabel("Organism", { exact: true })
      .fill("Coagulase negative Staphylococcus");
    await page.getByLabel("Colony count").fill("scanty");
    await page.getByLabel("Specimen").fill("blood");
    await page.getByLabel("Antibiotic 1 for organism 1").fill("Vancomycin");
    await page.getByRole("radio", { name: "S for Vancomycin" }).check();

    const preview = page.getByRole("complementary", { name: /predicted severity/i });
    await expect(preview.getByText("Needs follow-up", { exact: true })).toBeVisible();
    await expect(preview.getByText(/looks like contamination/i)).toBeVisible();
    // The tech is told, before saving, that this one stays open.
    await expect(preview.getByText(/would close the case:/i)).toBeVisible();
    await expect(preview.getByText("no", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    const resultId = newestResultFor(data.orderA);
    expect(await classified(resultId)).toBe("follow_up");

    const caseRow = query(
      `SELECT state || '|' || coalesce(severity, 'null') FROM pending_cases
        WHERE order_id = '${data.orderA}'`,
    );
    expect(caseRow).toBe("flagged|follow_up");
    // The whole point: a contaminant call is a judgement a human confirms.
    expect(
      query(
        `SELECT count(*) FROM pending_cases
          WHERE order_id = '${data.orderA}' AND closed_at IS NOT NULL`,
      ),
    ).toBe("0");
  });
});

// ── the properties that hold across all of it ─────────────────────────

test.describe("what the rule engine will not do", () => {
  test("a narrative on the same report keeps a closeable culture open", async ({
    page,
    request,
  }) => {
    const data = seed();
    await prescribe(request, data, "Monocef");
    await discharge(request, data);
    await openEntry(page, data, data.orderA);

    await page.getByRole("button", { name: "Add an organism" }).click();
    await page.getByLabel("Organism", { exact: true }).fill("Escherichia coli");
    await page.getByLabel("Colony count").fill(">100,000 CFU/mL");
    await page.getByLabel("Specimen").fill("urine");
    await page.getByLabel("Antibiotic 1 for organism 1").fill("Ceftriaxone");
    await page.getByRole("radio", { name: "S for Ceftriaxone" }).check();

    await page.getByRole("button", { name: "Add a section" }).click();
    await page.getByLabel("Text", { exact: true }).fill("Plenty of pus cells seen.");

    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    const resultId = newestResultFor(data.orderA);
    expect(await classified(resultId)).toBe("normal");

    // Normal, and still open. Narrative reports never auto-close.
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(
      query(`SELECT state FROM pending_cases WHERE order_id = '${data.orderA}'`),
    ).not.toBe("closed");
  });

  test("classification is idempotent under a redelivered wake-up", async ({
    page,
    request,
  }) => {
    const data = seed();
    await prescribe(request, data, "Monocef");
    await discharge(request, data);
    await openEntry(page, data, data.orderA);

    await page.getByRole("button", { name: "Add an organism" }).click();
    await page.getByLabel("Organism", { exact: true }).fill("Escherichia coli");
    await page.getByLabel("Colony count").fill(">100,000 CFU/mL");
    await page.getByLabel("Specimen").fill("urine");
    await page.getByLabel("Antibiotic 1 for organism 1").fill("Ceftriaxone");
    await page.getByRole("radio", { name: "R for Ceftriaxone" }).check();
    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    const resultId = newestResultFor(data.orderA);
    expect(await classified(resultId)).toBe("critical");

    // Hand the worker the same wake-up again, the way an at-least-once queue
    // eventually will.
    query(
      `SELECT pgmq.send('classify', '{"result_id": "${resultId}"}'::jsonb)`,
    );
    await new Promise((resolve) => setTimeout(resolve, 3000));

    expect(
      query(
        `SELECT count(*) FROM classifications WHERE result_id = '${resultId}'`,
      ),
    ).toBe("1");
    expect(
      query(
        `SELECT count(*) FROM case_events
          WHERE case_id = (SELECT id FROM pending_cases
                            WHERE order_id = '${data.orderA}')
            AND event_type = 'result_classified'`,
      ),
    ).toBe("1");
  });

  test("NODE B is not required for any of it", async ({ page, request }) => {
    // RULE 1 and RULE 2 at the same time. Classification is table lookups and
    // comparisons; the inference node is not consulted and does not need to
    // exist. The health endpoint reports it unreachable and the whole flow
    // still completes.
    const health = await (await request.get("/api/health")).json();
    expect(health.status).toBe("ok");
    expect(health.llm.reachable).toBe(false);

    const data = seed();
    await prescribe(request, data, "Monocef");
    await discharge(request, data);
    await openEntry(page, data, data.orderA);

    await page.getByRole("button", { name: "Add an organism" }).click();
    await page.getByLabel("Organism", { exact: true }).fill("Escherichia coli");
    await page.getByLabel("Colony count").fill(">100,000 CFU/mL");
    await page.getByLabel("Specimen").fill("urine");
    await page.getByLabel("Antibiotic 1 for organism 1").fill("Ceftriaxone");
    await page.getByRole("radio", { name: "R for Ceftriaxone" }).check();
    await page.getByRole("button", { name: "Save result" }).click();
    await expect(page.getByText(/result recorded/i)).toBeVisible();

    expect(await classified(newestResultFor(data.orderA))).toBe("critical");
  });
});
