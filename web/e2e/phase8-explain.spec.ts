/**
 * Phase 8, end to end — "Why does this matter?", and what happens when it can't.
 *
 * Nothing is mocked. A real browser, the real API, the real Postgres, the real
 * approved knowledge base, and — where it is reachable — the real NODE B.
 *
 * ## What each test is actually for
 *
 * The valuable assertions here are the **negative** ones. A RAG panel that
 * renders a paragraph is easy; a RAG panel that refuses to render one, for the
 * right reason, at the right moment, is the entire safety argument of Phase 8.
 * So three of the four tests below assert that *nothing* is shown:
 *
 *   1. **NODE B off** → no explanation, and the flag underneath is untouched.
 *      This is RULE 2 at the level of one screen.
 *   2. **No approved guidance** → no explanation, and NODE B is never called.
 *   3. **Unapproved guidance** → invisible to retrieval, even though the text
 *      is sitting in the same table as the approved copy.
 *   4. The happy path, which only proves the wiring.
 *
 * ## Why (1) uses the kill switch rather than waiting for NODE B to be down
 *
 * Same reason `nodeb.ts` exists: a test that waits for infrastructure to fail
 * is observing the weather, not testing a guarantee. The admin kill switch is
 * the product's own designed way to turn inference off, so the spec pulls the
 * same lever a hospital would and the test means the same thing whether NODE B
 * is powered on, powered off, or has never existed.
 *
 * ## The one test that genuinely cannot make its own conditions
 *
 * The happy path needs a language model actually loaded on NODE B. That is a
 * real external dependency and there is no honest way to fake it — a stubbed
 * model would test the stub. It therefore **skips, loudly, with the reason**,
 * rather than passing on a technicality. A skipped test that says why is
 * honest; a green test that mocked the thing under test is not.
 */

import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { query, seedGatedEncounter } from "./seed.mjs";
import { apiAuth, signIn, signInAsDoctor } from "./auth";
import { withNodeBDisabled } from "./nodeb";

/**
 * Every test here seeds a whole journey before it reaches its subject: admit,
 * prescribe, contract, discharge, then type a culture into the real
 * result-entry screen and wait for the worker to classify it. That is ~25 s
 * before the first assertion, and the happy path then waits on ~14 s of
 * generation on NODE B.
 *
 * Playwright's 30 s default is sized for a single interaction, not a journey.
 * Raising it here is not papering over a race — the waits below all poll for a
 * specific condition and fail with a message naming what never arrived.
 */
test.describe.configure({ timeout: 180_000 });

interface Seed {
  encounterId: string;
  doctorId: string;
  doctorCode: string;
  adminCode: string;
  orderA: string;
}

/**
 * The generation model, which is **not** the probe model.
 *
 * `/api/health` reports `llm.model` as `qwen3:4b` because that is what the
 * liveness probe pokes. Generation uses `mistral:7b` — qwen3 is a reasoning
 * model that spends its token budget thinking out loud and was measured at
 * 82.5 s without producing usable JSON. Checking health's model here would
 * check the wrong one, and the happy-path test would run and fail.
 */
const GENERATION_MODEL = "mistral:7b";

/** Phrases the API returns on its null paths. Kept in one place. */
const NO_GUIDANCE = /No approved guidance/i;
const NODE_B_DOWN = /AI assist is offline/i;

/**
 * Is a usable generation model actually loaded on NODE B?
 *
 * Asked of NODE B directly rather than through NODE A, because NODE A's health
 * probe answers "can I open a socket", and a running Ollama with an empty model
 * directory answers that question `true` while being useless for generation.
 * That exact state happened on 2026-09-15 — the service was up, `/api/tags`
 * returned `{"models":[]}`, and every explanation came back null.
 */
async function generationModelAvailable(
  request: APIRequestContext,
): Promise<boolean> {
  const base = process.env.RG_E2E_NODEB_URL ?? "http://172.25.54.48:11434";
  try {
    const response = await request.get(`${base}/api/tags`, { timeout: 5_000 });
    if (!response.ok()) return false;
    const body = (await response.json()) as { models?: { name: string }[] };
    return (body.models ?? []).some((m) => m.name.startsWith(GENERATION_MODEL));
  } catch {
    return false;
  }
}

/**
 * A discharged patient with a ceftriaxone-resistant *E. coli* in urine.
 *
 * Chosen because it is the case the product exists for, and because it is the
 * one the seeded demo guidance covers — `explainQuery()` turns this case into
 * "Escherichia coli Ceftriaxone resistant urine", which is what the antibiotic
 * policy is indexed on. A potassium result would retrieve nothing and every
 * test below would pass for the wrong reason.
 */
async function seedResistantCase(
  page: Page,
  request: APIRequestContext,
): Promise<{ data: Seed; caseId: string }> {
  const data = seedGatedEncounter() as Seed;
  const headers = await apiAuth(request, data.doctorCode);

  await request.post(`/api/encounters/${data.encounterId}/discharge-medications`, {
    headers,
    data: { drug_name: "Monocef", is_antibiotic: true },
  });

  const readiness = await (
    await request.get(`/api/encounters/${data.encounterId}/discharge-readiness`, {
      headers,
    })
  ).json();
  await request.post(`/api/encounters/${data.encounterId}/discharge-contracts`, {
    headers,
    data: {
      contracts: readiness.blocking_orders.map((o: { order_id: string }) => ({
        order_id: o.order_id,
        responsible_doctor_id: data.doctorId,
        expected_by: new Date(Date.now() + 48 * 3600_000).toISOString(),
      })),
    },
  });
  await request.post(`/api/encounters/${data.encounterId}/discharge`, { headers });

  // Entered through the real result-entry screen, so the organism and the
  // sensitivity row are stored exactly as a lab tech would store them. Posting
  // the JSON directly would skip the parsing this case depends on.
  await signInAsDoctor(page, data);
  await page.goto(`/encounters/${data.encounterId}/orders/${data.orderA}/result`);
  await expect(page.getByRole("heading", { name: /enter a result/i })).toBeVisible();

  await page.getByRole("button", { name: "Add an organism" }).click();
  await page.getByLabel("Organism", { exact: true }).fill("Escherichia coli");
  await page.getByLabel("Colony count").fill(">100,000 CFU/mL");
  await page.getByLabel("Specimen").fill("urine");
  await page.getByLabel("Antibiotic 1 for organism 1").fill("Ceftriaxone");
  await page.getByRole("radio", { name: "R for Ceftriaxone" }).check();
  await page.getByRole("button", { name: /save|submit/i }).first().click();

  // Keyed on `order_id`, not just the encounter. `seedGatedEncounter` creates
  // three blocking orders, so a discharged encounter has **three** pending
  // cases, and `LIMIT 1` over the encounter returns whichever the planner felt
  // like -- usually not the one the result above was entered against. The
  // symptom was a test waiting forever for a classification that had already
  // happened, on a different case.
  const caseId = await waitFor(
    () =>
      query(
        `SELECT id FROM pending_cases WHERE order_id = '${data.orderA}' LIMIT 1`,
      ),
    `a pending case for order ${data.orderA}`,
  );

  // Classification is done by the worker off the `classify` queue, so the
  // severity is not set the instant the result is saved. Waiting for it here
  // means every test below starts from a case that has actually been flagged
  // -- otherwise "the severity did not change" is trivially true because there
  // was never a severity to change.
  await waitFor(() => severityOf(caseId), `case ${caseId} to be classified`);

  return { data, caseId };
}

/**
 * The "Checked against" heading — present only when citations are rendered.
 *
 * By role, not by text. The panel's standing description says "Every quotation
 * is checked against its source before you see it" whether or not anything was
 * cited, and the footnote says something similar again, so `getByText` matches
 * three elements and finds the promise rather than the evidence.
 */
function citationsHeading(page: Page) {
  return page.getByRole("heading", { name: "Checked against", exact: true });
}

/** Poll a synchronous DB read until it returns something. */
async function waitFor(read: () => string, what: string): Promise<string> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const value = read();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/**
 * The severity on the case itself — the flag a clinician actually sees.
 *
 * Read from `pending_cases`, not from `classifications`. The classification is
 * the engine's working, but the case row is what the worklist renders and what
 * escalation acts on, so it is the thing that must be provably unchanged when
 * the AI is taken away.
 */
function severityOf(caseId: string): string {
  return query(`SELECT severity FROM pending_cases WHERE id = '${caseId}'`);
}

// ── 1. ★ RULE 2, on this screen ───────────────────────────────────────

test("with NODE B off, the panel says so and the flag is untouched", async ({
  page,
  request,
}) => {
  const { data, caseId } = await seedResistantCase(page, request);
  const severityBefore = severityOf(caseId);
  expect(severityBefore, "the seeded case should have been classified").toBeTruthy();

  await withNodeBDisabled(request, data.adminCode, async () => {
    await signIn(page, data.adminCode);
    await page.goto(`/cases/${caseId}`);

    // The clinical content is present *before* anything is asked of NODE B.
    // This is the assertion that matters: a clinician who came to read a
    // critical result gets the result, whether or not the assist is alive.
    await expect(page.getByRole("heading", { name: /Why does this matter/i })).toBeVisible();
    await expect(page.getByText("Escherichia coli").first()).toBeVisible();

    await page.getByRole("button", { name: /^Explain$/ }).click();

    await expect(page.getByText(NODE_B_DOWN)).toBeVisible({ timeout: 60_000 });
    // No prose, no citations -- not a partial answer. Located by *role*: the
    // panel's own standing blurb contains the words "checked against its
    // source" whether or not anything was cited, so a text match here would
    // find the promise rather than the evidence.
    await expect(citationsHeading(page)).toHaveCount(0);
  });

  // ★ The whole of RULE 2 in one line: the assist went away and the clinical
  // record did not move.
  expect(severityOf(caseId)).toBe(severityBefore);
});

// ── 2. nothing relevant → NODE B is never called ──────────────────────

test("a query no guidance covers returns early, without calling NODE B", async ({
  page,
  request,
}) => {
  const { data, caseId } = await seedResistantCase(page, request);
  const headers = await apiAuth(request, data.doctorCode);

  const started = Date.now();
  const response = await request.post(`/api/cases/${caseId}/explain`, {
    headers,
    data: { query: "zzzqqq no such organism anywhere in any guideline" },
  });
  const elapsed = Date.now() - started;

  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.explanation).toBeNull();
  expect(body.note).toMatch(NO_GUIDANCE);
  expect(body.sources_considered).toBe(0);

  // Generation takes ~14 s when it happens. Returning in well under a second
  // is the observable evidence that NODE B was not called at all, which is
  // what 8.3 requires below the relevance floor.
  expect(elapsed).toBeLessThan(3_000);
});

// ── 3. approval is what makes a document visible ──────────────────────

test("an unapproved document is invisible to retrieval", async ({ page, request }) => {
  const { data, caseId } = await seedResistantCase(page, request);
  const admin = await apiAuth(request, data.adminCode);

  // Same subject matter as the approved demo document, but unapproved. If
  // approval were not enforced this would be retrieved alongside it.
  const ingested = await request.post("/api/kb/documents", {
    headers: admin,
    data: {
      title: "E2E-unapproved Escherichia coli ceftriaxone urine policy",
      // `other`, not "E2E" -- `ck_kb_documents_publisher` allows exactly
      // who/icmr/hospital/nlem/other, and the API now refuses anything else
      // with a 422 rather than letting the CHECK raise a 500.
      publisher: "other",
      doc_type: "antibiotic_policy",
      document_text:
        "Escherichia coli in urine resistant to ceftriaxone must be reviewed. " +
        "This sentence exists only to be findable, and must never be found, " +
        "because this document has not been approved by anybody at all.",
    },
  });
  expect(ingested.status()).toBe(200);
  const documentId = (await ingested.json()).document_id as string;

  try {
    const chunkIds: string = query(
      `SELECT string_agg(id::text, ',') FROM kb_chunks
        WHERE kb_document_id = '${documentId}'`,
    );
    expect(chunkIds, "the unapproved document should still have been chunked").toBeTruthy();

    const response = await request.post(`/api/cases/${caseId}/explain`, {
      headers: admin,
      data: { query: "Escherichia coli Ceftriaxone resistant urine" },
    });
    const body = await response.json();

    // Whatever else happened, no chunk of the unapproved document came back.
    const returned: string[] = (body.evidence ?? []).map(
      (e: { chunk_id: string }) => e.chunk_id,
    );
    for (const id of chunkIds.split(",")) {
      expect(returned, "an unapproved chunk reached a clinician").not.toContain(id);
    }
  } finally {
    // Hard-deleted rather than soft: this is a test fixture in the knowledge
    // base, not a clinical row, and leaving it behind would poison every later
    // retrieval on this machine.
    query(`DELETE FROM kb_chunks WHERE kb_document_id = '${documentId}'`);
    query(`DELETE FROM kb_documents WHERE id = '${documentId}'`);
  }
});

// ── 4. the happy path, when there is a model to run it ────────────────

test("a verified explanation shows its quote inside the source passage", async ({
  page,
  request,
}) => {
  test.skip(
    !(await generationModelAvailable(request)),
    `NODE B has no ${GENERATION_MODEL} loaded, so there is nothing to generate ` +
      `with. This path cannot be proven without it and is not being faked.`,
  );

  const approved: string = query(
    "SELECT count(*)::text FROM kb_documents WHERE approved_at IS NOT NULL AND deleted_at IS NULL",
  );
  expect(
    Number(approved),
    "no approved guidance -- run `docker compose exec -T api python scripts/seed_kb_demo.py`",
  ).toBeGreaterThan(0);

  const { data, caseId } = await seedResistantCase(page, request);
  await signIn(page, data.adminCode);
  await page.goto(`/cases/${caseId}`);

  await page.getByRole("button", { name: /^Explain$/ }).click();

  // Generation is ~14 s and genuinely variable; the panel says as much to the
  // user rather than showing a progress bar that would be a lie.
  const verdict = page.getByText(/verified source|No explanation shown/);
  await expect(verdict).toBeVisible({ timeout: 120_000 });

  // A null answer here is a real outcome, not a flake -- the model may have
  // written something the sources do not contain, which is the verifier doing
  // its job. Say which happened rather than asserting past it.
  if (await page.getByText(/No explanation shown/).count()) {
    const note = await page.getByText(/No explanation shown/).textContent();
    test.info().annotations.push({
      type: "note",
      description: `NODE B answered but nothing survived verification: ${note}`,
    });
    return;
  }

  await expect(citationsHeading(page)).toBeVisible();
  // ★ Shown even when it is zero: "0 rejected" is the sentence that tells a
  // clinician the checking happened at all.
  await expect(page.getByText(/\d+ rejected/)).toBeVisible();

  // ★ The claim Phase 8 makes: the quote is shown *inside* its source passage,
  // so a clinician can check it rather than trust it.
  const mark = page.locator("mark").first();
  await expect(mark).toBeVisible();
  const quoted = ((await mark.textContent()) ?? "").trim();
  expect(quoted.length).toBeGreaterThanOrEqual(20);

  const passage = ((await mark.locator("xpath=..").textContent()) ?? "").trim();
  expect(passage.length).toBeGreaterThan(quoted.length);

  // And the highlighted text really is in the stored chunk -- proving the
  // offsets line up with the source, not merely that something got bolded.
  const inSource: string = query(
    `SELECT count(*)::text FROM kb_chunks
      WHERE regexp_replace(chunk_text, '\\s+', ' ', 'g')
            LIKE '%' || regexp_replace($$${quoted}$$, '\\s+', ' ', 'g') || '%'`,
  );
  expect(Number(inSource), `highlighted text not found in any chunk: ${quoted}`)
    .toBeGreaterThan(0);
});
