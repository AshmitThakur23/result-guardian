/**
 * Phase 8.1 — the Guidance screen, and the approval that makes a document real.
 *
 * ## The loop this proves
 *
 * A person pastes a policy, approves it, and the Explain panel can quote it.
 * Until the screen existed, the only way to load guidance was to call the API
 * by hand, so the product could only ever explain whatever a seed script had
 * loaded. That loop is what test 2 walks, end to end, through the browser.
 *
 * ## Why these run without NODE B
 *
 * **Retrieval is entirely NODE A** — Postgres full-text over approved chunks,
 * no model, no network hop. So "did approving this document make it findable?"
 * is answerable with NODE B switched off, unplugged, or never built. The tests
 * assert `sources_considered`, which counts what retrieval found, rather than
 * the explanation text, which needs a model.
 *
 * That is not a workaround for a flaky dependency. It is the same claim RULE 2
 * makes, and asserting it here means these tests mean the same thing on a
 * laptop with no GPU as they do on the demo machine.
 *
 * ## The negative test is the important one
 *
 * Test 2 checks the document is **invisible while unapproved** before it checks
 * that approving reveals it. A test that only checked the second half would
 * pass just as well if approval did nothing at all.
 */

import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

// @ts-expect-error -- plain ESM helper
import { query, seedGatedEncounter } from "./seed.mjs";
import { apiAuth, signIn } from "./auth";

interface Seed {
  adminCode: string;
  doctorCode: string;
  doctorId: string;
  encounterId: string;
}

/** Discharge through the real gate, so a pending case exists to open. */
async function discharge(
  request: APIRequestContext,
  data: Seed,
): Promise<void> {
  const headers = await apiAuth(request, data.doctorCode);
  const readiness = await (
    await request.get(`/api/encounters/${data.encounterId}/discharge-readiness`, {
      headers,
    })
  ).json();
  await request.post(`/api/encounters/${data.encounterId}/discharge-contracts`, {
    headers,
    data: {
      contracts: (readiness.blocking_orders as { order_id: string }[]).map((o) => ({
        order_id: o.order_id,
        responsible_doctor_id: data.doctorId,
        expected_by: new Date(Date.now() + 48 * 3600_000).toISOString(),
      })),
    },
  });
  expect(
    (await request.post(`/api/encounters/${data.encounterId}/discharge`, { headers }))
      .status(),
  ).toBe(200);
}

// Adding a document and approving it are two round trips through the browser,
// and the worklist test backdates rows between assertions. The 30 s default is
// sized for one interaction.
test.describe.configure({ timeout: 120_000 });

/**
 * A phrase that exists in no other document in the knowledge base.
 *
 * Unique per run: the demo fixture is about ceftriaxone and E. coli, so a test
 * querying those terms would retrieve *it* and pass whether or not the document
 * under test was ever stored. The nonsense token is what makes
 * `sources_considered` a statement about this document specifically.
 */
function uniqueTerm(): string {
  return `zylophosphamide${Date.now().toString(36)}`;
}

/**
 * How many approved passages retrieval finds for a query. Needs no model.
 *
 * Takes a **real** `caseId`. The endpoint refuses an unknown case with a 404
 * before doing any work, so passing a random UUID here would measure the
 * existence guard instead of retrieval. It used to pass a random one, and that
 * is how a genuine 500 -- `ai_rejections` has a foreign key to `pending_cases`
 * -- hid as a 1-in-3 flaky test.
 */
async function sourcesFor(
  request: APIRequestContext,
  headers: Record<string, string>,
  caseId: string,
  question: string,
): Promise<number> {
  const response = await request.post(`/api/cases/${caseId}/explain`, {
    headers,
    data: { query: question },
  });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()).sources_considered as number;
}

// ── 1. who may load guidance ──────────────────────────────────────────

test("a doctor cannot reach the Guidance screen", async ({ page }) => {
  const data = seedGatedEncounter() as Seed;
  await signIn(page, data.doctorCode);
  await page.goto("/knowledge-base");

  // The route guard must refuse. Asserted by absence of the form rather than
  // by URL, because a guard that renders nothing and a guard that redirects are
  // both acceptable -- what is not acceptable is the form appearing.
  await expect(page.getByRole("heading", { name: "Add guidance" })).toHaveCount(0);
  await expect(
    page.getByRole("textbox", { name: /^Text$/ }),
  ).toHaveCount(0);
});

// ── 2. ★ the loop: paste, approve, and it becomes quotable ────────────

test("guidance pasted in the UI is invisible until approved, then retrievable", async ({
  page,
  request,
}) => {
  const data = seedGatedEncounter() as Seed;
  const term = uniqueTerm();
  const title = `E2E guidance ${term}`;
  const headers = await apiAuth(request, data.adminCode);

  // A real case to ask against -- see `sourcesFor`.
  await discharge(request, data);
  const caseId = query(
    `SELECT id FROM pending_cases WHERE encounter_id = '${data.encounterId}' LIMIT 1`,
  );
  expect(caseId, "discharge produced no pending case").toBeTruthy();

  // Nothing in the knowledge base mentions this word yet.
  expect(await sourcesFor(request, headers, caseId, term)).toBe(0);

  await signIn(page, data.adminCode);
  await page.goto("/knowledge-base");

  await page.getByRole("heading", { name: "Add guidance" }).waitFor();
  await page.getByLabel("Title").fill(title);
  await page
    .getByLabel("Text")
    .fill(
      `Section 9 Investigational therapy\n\n` +
        `Patients receiving ${term} require review of renal function before ` +
        `the next dose is given. The responsible clinician must be informed ` +
        `within twenty-four hours of the result becoming available.`,
    );
  await page.getByRole("button", { name: "Add, unapproved" }).click();

  const row = page.getByRole("listitem").filter({ hasText: title });
  await expect(row).toBeVisible();
  // `exact`, and it matters more than it looks. `getByText("Retrievable")`
  // does case-insensitive SUBSTRING matching, so it also matches "Not
  // retrievable" -- the approved-state assertion below passed instantly,
  // before the click had taken effect, and the test failed one line later on
  // a document that genuinely was not approved yet.
  await expect(row.getByText("Not retrievable", { exact: true })).toBeVisible();

  try {
    // ★ The negative half. Stored, chunked, and still invisible -- because it
    // has not been approved. Without this assertion the test would pass even
    // if approval were a no-op.
    expect(
      await sourcesFor(request, headers, caseId, term),
      "an unapproved document reached retrieval",
    ).toBe(0);

    await row.getByRole("button", { name: "Approve" }).click();
    // Exact again: "Not retrievable" must no longer be present, and the bare
    // word must be. Asserting the disappearance is what proves the click did
    // something, rather than that a substring happened to still be on screen.
    await expect(row.getByText("Not retrievable", { exact: true })).toHaveCount(0);
    await expect(row.getByText("Retrievable", { exact: true })).toBeVisible();

    // ★ The positive half. A person pasted a policy and the Explain panel can
    // now quote it -- the whole point of the screen.
    expect(
      await sourcesFor(request, headers, caseId, term),
      "approving did not make the document retrievable",
    ).toBeGreaterThan(0);
  } finally {
    // Soft-deleted: there is no DELETE endpoint for guidance yet, and leaving
    // a test document approved would poison every later retrieval on this
    // machine. `kb_chunks` has no `deleted_at`, but retrieval joins through
    // `kb_documents` and filters on its, so this is sufficient.
    query(`UPDATE kb_documents SET deleted_at = now() WHERE title = '${title}'`);
  }

  // And once removed it is invisible again, which also proves the cleanup
  // above actually works rather than merely running.
  expect(await sourcesFor(request, headers, caseId, term)).toBe(0);
});

// ── 3. the typed question reaches retrieval ───────────────────────────

test("a typed question searches the guidance, not the patient's report", async ({
  page,
  request,
}) => {
  const data = seedGatedEncounter() as Seed;
  const headers = await apiAuth(request, data.adminCode);

  // The seeded demo policy is about ceftriaxone-resistant E. coli in urine.
  const approved: string = query(
    "SELECT count(*)::text FROM kb_documents " +
      " WHERE approved_at IS NOT NULL AND deleted_at IS NULL",
  );
  test.skip(
    Number(approved) === 0,
    "no approved guidance loaded -- run scripts/seed_kb_demo.py",
  );

  // A real case, for the same reason as above.
  await discharge(request, data);
  const caseId = query(
    `SELECT id FROM pending_cases WHERE encounter_id = '${data.encounterId}' LIMIT 1`,
  );
  expect(caseId, "discharge produced no pending case").toBeTruthy();

  // A question in the clinician's own words, not the structured query the
  // panel builds. This is what the new input box sends.
  expect(
    await sourcesFor(
      request,
      headers,
      caseId,
      "why does ceftriaxone resistance matter",
    ),
    "a typed question found no guidance",
  ).toBeGreaterThan(0);

  // ★ And the boundary that matters: it searches approved guidance, never the
  // patient's own report. A question about this patient finds nothing, and
  // that is the correct answer rather than a failure.
  expect(
    await sourcesFor(
      request,
      headers,
      caseId,
      "what is this patient's home address",
    ),
  ).toBe(0);

  // The box is on the case screen and carries that caveat in words.
  await signIn(page, data.adminCode);
  await page.goto(`/cases/${caseId}`);
  await expect(
    page.getByText(/approved guidance — not this patient's report/i),
  ).toBeVisible();
});

// ── 4. the worklist date filter actually filters ──────────────────────

test("the Opened filter hides cases outside the window", async ({ request }) => {
  const data = seedGatedEncounter() as Seed & { doctorId: string };
  const headers = await apiAuth(request, data.doctorCode);

  // Discharge so there are cases, then age one deliberately. Backdating is the
  // only honest way to test a date filter -- a suite that waits sixty days is
  // not a suite, and one that only ever queries "today" proves nothing.
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
  expect(
    (await request.post(`/api/encounters/${data.encounterId}/discharge`, { headers }))
      .status(),
  ).toBe(200);

  const caseId = query(
    `SELECT id FROM pending_cases WHERE encounter_id = '${data.encounterId}'
      ORDER BY created_at LIMIT 1`,
  );
  expect(caseId, "discharge produced no pending case").toBeTruthy();

  query(
    `UPDATE pending_cases SET opened_at = now() - interval '60 days'
      WHERE id = '${caseId}'`,
  );

  const idsFor = async (params: string): Promise<string[]> => {
    const response = await request.get(
      `/api/worklist?limit=200&include_closed=true&${params}`,
      { headers },
    );
    expect(response.status(), await response.text()).toBe(200);
    return ((await response.json()).rows as { case_id: string }[]).map(
      (row) => row.case_id,
    );
  };

  const weekAgo = new Date(Date.now() - 7 * 86_400_000).toISOString();
  const quarterAgo = new Date(Date.now() - 90 * 86_400_000).toISOString();

  // ★ Both halves. "This week" must HIDE the 60-day-old case, and "3 months"
  // must SHOW it. Asserting only the second would pass even if `opened_from`
  // were ignored entirely -- which is exactly what was happening before the
  // control existed.
  expect(
    await idsFor(`opened_from=${encodeURIComponent(weekAgo)}`),
    "a 60-day-old case appeared under 'This week'",
  ).not.toContain(caseId);

  expect(
    await idsFor(`opened_from=${encodeURIComponent(quarterAgo)}`),
    "a 60-day-old case was missing from 'Last 3 months'",
  ).toContain(caseId);
});
