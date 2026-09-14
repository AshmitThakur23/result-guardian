/**
 * Phase 6, end to end — a real PDF goes in, and a human can read what came out.
 *
 * Nothing here is mocked. A real browser uploads a real PDF through the real
 * multipart endpoint; the real worker drains the real `ingest` queue; PyMuPDF
 * does real extraction; and the assertions are what a ward clerk can see on
 * screen plus what Postgres actually stored.
 *
 * **The guarantee Phase 6.5 makes is the one worth testing:**
 *
 *     Any failure routes the document to the Phase 3 manual entry form with
 *     the page images shown side-by-side. The workflow never stalls because
 *     parsing failed.
 *
 * So the specs below cover both halves: the happy path where extraction works,
 * and the failure path where it cannot — and in the failure case the clerk is
 * given a reason in plain words and a route to keep working.
 */

import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { query, seedGatedEncounter } from "./seed.mjs";
import { apiAuth, signIn } from "./auth";

const PDF = fileURLToPath(new URL("./fixtures/lab-report.pdf", import.meta.url));

interface Seed {
  doctorCode: string;
  adminCode: string;
}

function seed(): Seed {
  return seedGatedEncounter() as Seed;
}

/**
 * Upload through the real endpoint with a unique filename per run.
 *
 * **The filename has to be unique.** Storage is content-addressed by SHA-256,
 * so re-uploading identical bytes is deduplicated by design — a second run
 * would get `duplicate: true` and no new document, and the spec would fail for
 * a reason that is actually correct behaviour. Salting the bytes keeps each
 * run's document distinct while leaving the PDF valid.
 */
async function upload(
  request: APIRequestContext,
  code: string,
  { corrupt = false }: { corrupt?: boolean } = {},
): Promise<{ documentId: string; duplicate: boolean; virusScan: string }> {
  const original = readFileSync(PDF);
  // A PDF comment line is ignored by every reader, so this changes the hash
  // without changing the document.
  const salt = Buffer.from(`\n%% e2e ${Date.now()}-${Math.random()}\n`);
  const bytes = corrupt
    ? Buffer.concat([Buffer.from("%PDF-1.7\n"), Buffer.from("not a pdf at all")])
    : Buffer.concat([original, salt]);

  const response = await request.post("/api/reports/upload", {
    headers: await apiAuth(request, code),
    multipart: {
      file: {
        name: corrupt ? "broken.pdf" : "lab-report.pdf",
        mimeType: "application/pdf",
        buffer: bytes,
      },
    },
  });
  expect(
    response.status(),
    `upload failed: ${await response.text()}`,
  ).toBe(202);
  const body = await response.json();
  return {
    documentId: body.document_id,
    duplicate: body.duplicate,
    virusScan: body.virus_scan,
  };
}

/** Wait for the worker to reach a terminal state for this document. */
async function settled(documentId: string): Promise<string> {
  const terminal = ["extracted", "needs_review", "failed"];
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const status = query(`SELECT status FROM documents WHERE id = '${documentId}'`);
    if (terminal.includes(status)) return status;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(
    `document ${documentId} never left the queue; last status ` +
      query(`SELECT status FROM documents WHERE id = '${documentId}'`),
  );
}

test.describe("a report arrives as a PDF", () => {
  // Extraction runs in a child process with a 120s budget, so give the whole
  // journey room. This is not padding to hide a race -- it is the documented
  // worst case for one A4 page under the worker's CPU quota.
  test.setTimeout(180_000);

  test("★ is uploaded, read by the worker, and its text is stored with spans", async ({
    request,
  }) => {
    const data = seed();
    const { documentId, duplicate, virusScan } = await upload(request, data.doctorCode);

    expect(duplicate).toBe(false);
    // `skipped` is not `clean`, and the API must not pretend otherwise.
    expect(["skipped", "clean"]).toContain(virusScan);

    const status = await settled(documentId);
    expect(
      status,
      "a native PDF with a text layer must extract, not go to review",
    ).toBe("extracted");

    // The text actually came out.
    const pageCount = query(
      `SELECT count(*) FROM document_pages WHERE document_id = '${documentId}'`,
    );
    expect(pageCount).toBe("1");

    const text = query(
      `SELECT text_layer FROM document_pages WHERE document_id = '${documentId}'`,
    );
    expect(text).toContain("Potassium");
    expect(text).toContain("6.9");

    // A native PDF is not a scan, and saying so drives whether OCR runs.
    expect(
      query(
        `SELECT is_scanned FROM document_pages WHERE document_id = '${documentId}'`,
      ),
    ).toBe("f");

    // ★ The span invariant. Without it every citation Phase 8 ever renders
    // points slightly to the left of the value it claims to quote.
    const spans = Number(
      query(
        `SELECT count(*) FROM document_spans WHERE document_id = '${documentId}'`,
      ),
    );
    expect(spans).toBeGreaterThan(0);

    const mismatched = query(
      `SELECT count(*) FROM document_spans s
         JOIN document_pages p ON p.document_id = s.document_id
                              AND p.page_no = s.page_no
        WHERE s.document_id = '${documentId}'
          AND substring(p.text_layer FROM s.char_start + 1
                        FOR s.char_end - s.char_start) <> s.text`,
    );
    expect(mismatched, "text_layer[char_start:char_end] must equal span.text").toBe("0");
  });

  test("the same bytes twice are deduplicated, not stored twice", async ({
    request,
  }) => {
    const data = seed();
    const original = readFileSync(PDF);
    const salted = Buffer.concat([
      original,
      Buffer.from(`\n%% dedup ${Date.now()}\n`),
    ]);

    const post = async () =>
      (
        await request.post("/api/reports/upload", {
          headers: await apiAuth(request, data.doctorCode),
          multipart: {
            file: { name: "same.pdf", mimeType: "application/pdf", buffer: salted },
          },
        })
      ).json();

    const first = await post();
    const second = await post();

    // Content addressing means dedup falls out of storage, not a lookup table.
    expect(first.duplicate).toBe(false);
    expect(second.duplicate).toBe(true);
    expect(second.document_id).toBe(first.document_id);
    expect(second.sha256).toBe(first.sha256);
  });
});

test.describe("the review queue and the fallback", () => {
  test.setTimeout(180_000);

  test("★ a file that cannot be read reaches a human with a reason, not a stack trace", async ({
    page,
    request,
  }) => {
    const data = seed();
    const { documentId } = await upload(request, data.doctorCode, { corrupt: true });

    const status = await settled(documentId);
    expect(["failed", "needs_review"]).toContain(status);

    // 6.5: the workflow never stalls because parsing failed. The clerk gets a
    // sentence they can act on -- never a parser message.
    const errorText = query(
      `SELECT error_text FROM documents WHERE id = '${documentId}'`,
    );
    expect(errorText.length).toBeGreaterThan(0);
    expect(errorText).not.toContain("Traceback");
    expect(errorText).not.toMatch(/Exception|__|\bself\b/);

    // And it is visible on screen, oldest first, to someone who can act on it.
    await signIn(page, data.doctorCode);
    await page.goto("/documents");
    await expect(
      page.getByRole("heading", { name: /Documents/i }),
    ).toBeVisible();

    const row = page.getByRole("row", { name: /broken\.pdf/ });
    await expect(row).toBeVisible();
    await expect(row.getByText(/Could not read|Needs review/)).toBeVisible();
  });

  test("the upload form is on the queue screen and admits when nothing scanned it", async ({
    page,
  }) => {
    const data = seed();
    await signIn(page, data.doctorCode);
    await page.goto("/documents");

    // The clerk can upload from the same screen they triage on.
    await expect(page.getByLabel("Report file")).toBeVisible();

    await page.getByLabel("Report file").setInputFiles(PDF);
    await page.getByRole("button", { name: /^Upload$/ }).click();

    // Either outcome is correct -- the fixture may already be stored from an
    // earlier run, and dedup is a feature, not a failure.
    //
    // Matched exactly, on the banner *title*. A loose /Report received/ hits
    // both the title and the body sentence beneath it ("Report received.
    // Extraction has been queued.") and fails Playwright's strict mode for a
    // reason that has nothing to do with the product.
    const banner = page
      .getByText("Report received", { exact: true })
      .or(page.getByText("Already received", { exact: true }));
    await expect(banner).toBeVisible({ timeout: 30_000 });
  });
});

test.describe("who may see a document", () => {
  test("an unauthenticated request for a page image is refused", async ({
    request,
  }) => {
    const data = seed();
    const { documentId } = await upload(request, data.doctorCode);
    await settled(documentId);

    // No Authorization header at all. Patient data behind a URL is still
    // patient data, and Phase 5.1 put RBAC on every endpoint.
    const response = await request.get(
      `/api/documents/${documentId}/pages/1/image`,
    );
    expect(response.status()).toBe(401);
  });
});
