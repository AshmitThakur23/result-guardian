/**
 * Phase 6 — one document: its pages, the highlight overlay, and the fallback.
 *
 *     **Fallback is mandatory:** any failure routes the document to the Phase
 *     3 manual entry form with **the page images shown side-by-side**. **The
 *     workflow never stalls because parsing failed.**
 *
 * This screen is the second half of that sentence, and the layout *is* the
 * requirement: page image on the left, what we read on the right, and a link
 * into the Phase 3 entry form that was the whole workflow before Phase 6
 * existed.
 *
 * **Low-confidence text is shown, and labelled.** 6.4 routes a page under
 * 0.70 to review *"instead of guessing"* — but hiding what OCR read would
 * make the clerk retype a page that is 70% right. So it is displayed with an
 * explicit warning that every value must be checked against the image. Shown
 * and doubted, never hidden and never trusted.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  useDocument,
  useDocumentSpans,
  useRetryDocument,
} from "../api/queries6";
import type { DocumentPage } from "../api/types6";
import { OCR_CONFIDENCE_FLOOR } from "../api/types6";
import { PageImage } from "../components/PageImage";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { ErrorState, Loading } from "../components/ui/States";

function ConfidenceNote({ page }: { page: DocumentPage }) {
  if (!page.is_scanned) {
    return (
      <p className="text-xs text-ink-muted">
        This page had a text layer, so it was read directly — no scanning
        involved.
      </p>
    );
  }
  if (page.ocr_confidence === null) {
    return (
      <p className="text-xs text-ink-muted">
        This page was scanned and nothing could be read from it.
      </p>
    );
  }
  const percent = Math.round(page.ocr_confidence * 100);
  const low = page.ocr_confidence < OCR_CONFIDENCE_FLOOR;
  return (
    <p className={`text-xs ${low ? "text-followup-text" : "text-ink-muted"}`}>
      Scanned page, read with {percent}% confidence
      {low
        ? ` — below the ${Math.round(OCR_CONFIDENCE_FLOOR * 100)}% threshold. Check every value against the image.`
        : "."}
    </p>
  );
}

export function DocumentDetailPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const document = useDocument(documentId);
  const [pageNo, setPageNo] = useState(1);
  const [search, setSearch] = useState("");
  const spans = useDocumentSpans(documentId, pageNo);
  const retry = useRetryDocument();

  if (document.isLoading) return <Loading label="Loading the document" />;
  if (document.isError || !document.data) {
    return (
      <ErrorState
        error={document.error}
        onRetry={() => void document.refetch()}
        fallbackTitle="This document could not be loaded"
      />
    );
  }

  const doc = document.data;
  const pages = doc.pages ?? [];
  const page = pages.find((p) => p.page_no === pageNo) ?? pages[0] ?? null;
  const unresolved = doc.status === "failed" || doc.status === "needs_review";
  const inFlight = doc.status === "received" || doc.status === "extracting";

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">
            {doc.original_filename ?? "Untitled report"}
          </h1>
          <p className="mt-1 text-sm text-ink-body">
            Received {new Date(doc.received_at).toLocaleString("en-IN")} ·{" "}
            {doc.page_count ?? pages.length} pages · via{" "}
            {doc.source_channel.replace("_", " ")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link className="text-sm underline" to="/documents">
            Back to reports
          </Link>
          {/* 6.5's admin retry button. Allowed even on a document that read
              cleanly -- a threshold may have moved, or the OCR model been
              updated, and re-running is the only way to benefit from that. */}
          <Button
            variant="secondary"
            disabled={inFlight || retry.isPending}
            onClick={() => documentId && retry.mutate(documentId)}
          >
            {retry.isPending ? "Queued…" : "Try reading it again"}
          </Button>
        </div>
      </header>

      {inFlight && (
        <Banner tone="info" title="Still being read">
          This report is being processed. The page will update on its own.
        </Banner>
      )}

      {retry.isError && (
        <Banner tone="danger" title="It could not be queued again">
          {retry.error instanceof Error
            ? retry.error.message
            : "Try again in a moment."}
        </Banner>
      )}

      {/* ★ The mandatory fallback. This banner is the route back to the
          workflow that existed before Phase 6, and it is shown whenever
          extraction did not fully succeed. */}
      {unresolved && (
        <Banner
          tone={doc.status === "failed" ? "danger" : "warning"}
          title={
            doc.status === "failed"
              ? "This report could not be read automatically"
              : "This report needs to be checked by a person"
          }
        >
          <p>{doc.error_text ?? "Check the pages below against the report."}</p>
          <p className="mt-2">
            The pages are shown below. Enter the result by hand from the
            patient&rsquo;s encounter — the report does not have to be readable
            for the result to be recorded.
          </p>
          <p className="mt-1">
            <Link className="font-medium underline" to="/patients">
              Find the patient and enter the result
            </Link>
          </p>
        </Banner>
      )}

      {pages.length === 0 ? (
        <Banner tone="warning" title="No pages were produced">
          Nothing could be read from this file, and no page images were
          rendered. Ask the lab to send the report again.
        </Banner>
      ) : (
        <>
          {pages.length > 1 && (
            <nav aria-label="Pages" className="flex flex-wrap gap-1">
              {pages.map((p) => (
                <button
                  key={p.page_no}
                  type="button"
                  aria-current={p.page_no === pageNo ? "page" : undefined}
                  onClick={() => setPageNo(p.page_no)}
                  className={`rounded border px-3 py-1 text-sm ${
                    p.page_no === pageNo
                      ? "border-slate-800 bg-slate-800 text-ink-inverse"
                      : "border-line bg-surface text-ink-body"
                  }`}
                >
                  Page {p.page_no}
                </button>
              ))}
            </nav>
          )}

          {page && (
            // Side by side on a wide screen, stacked on a narrow one. A ward
            // desk is not always a big monitor.
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <h2 className="mb-2 text-sm font-semibold text-ink">
                  Page {page.page_no} as received
                </h2>
                {page.image_url ? (
                  <PageImage
                    documentId={doc.id}
                    pageNo={page.page_no}
                    widthPt={page.width_pt}
                    heightPt={page.height_pt}
                    spans={spans.data?.spans ?? []}
                    highlight={search.trim() || null}
                  />
                ) : (
                  <Banner tone="info" title="No image for this page">
                    This page had a text layer and read cleanly, so no image was
                    rendered for it.
                  </Banner>
                )}
              </div>

              <div>
                <div className="mb-2 flex items-baseline justify-between gap-2">
                  <h2 className="text-sm font-semibold text-ink">
                    What was read
                  </h2>
                  {page.image_url && (
                    <label className="text-xs text-ink-body">
                      Highlight{" "}
                      <input
                        type="text"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder="e.g. 9.2"
                        className="w-28 rounded border border-line px-2 py-0.5 text-xs"
                      />
                    </label>
                  )}
                </div>
                <ConfidenceNote page={page} />
                <pre className="mt-2 max-h-[32rem] overflow-auto whitespace-pre-wrap rounded border border-line bg-surface-sunken p-3 text-xs text-ink">
                  {page.text_layer?.trim() ||
                    "Nothing could be read from this page."}
                </pre>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
