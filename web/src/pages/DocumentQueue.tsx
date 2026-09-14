/**
 * Phase 6 — the document review queue, and the upload form.
 *
 *     **Fallback is mandatory:** any failure routes the document to the Phase
 *     3 manual entry form with the page images shown side-by-side. **The
 *     workflow never stalls because parsing failed.**
 *
 * This screen is the front half of that sentence. Every document extraction
 * could not handle lands here, oldest first, with the reason written in
 * language a ward clerk can act on — never a parser message.
 *
 * **Oldest first is deliberate.** A backlog served newest-first grows a tail
 * of documents nobody ever reaches, and the ones at the tail are exactly the
 * ones that have been waiting longest for somebody to read them.
 */

import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useReviewQueue, useUploadReport } from "../api/queries6";
import type { DocumentRow } from "../api/types6";
import { Badge } from "../components/ui/Badge";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { ErrorState, Loading } from "../components/ui/States";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function StatusBadge({ status }: { status: DocumentRow["status"] }) {
  const tone =
    status === "failed"
      ? "critical"
      : status === "needs_review"
        ? "followup"
        : status === "extracted"
          ? "normal"
          : "info";
  const label =
    status === "needs_review"
      ? "Needs review"
      : status === "failed"
        ? "Could not read"
        : status.charAt(0).toUpperCase() + status.slice(1);
  // The shared Badge, which sets `whitespace-nowrap`. Hand-rolled, this pill
  // broke across two lines in a narrow column and read as a rendering fault --
  // visible only in a screenshot, never in a passing test.
  return (
    <Badge tone={tone} dot>
      {label}
    </Badge>
  );
}

function UploadPanel() {
  const upload = useUploadReport();
  const inputRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<File | null>(null);

  return (
    <section className="rounded border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">Upload a report</h2>
      <p className="mt-1 text-sm text-ink-body">
        PDF, PNG, JPEG or TIFF, up to 25 MB. The file is checked and queued for
        reading; you do not have to wait here.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <input
          ref={inputRef}
          type="file"
          // The server sniffs the real type from the bytes regardless of what
          // this filter lets through -- it is a convenience for the person
          // picking a file, never a security control.
          accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"
          aria-label="Report file"
          className="text-sm"
          onChange={(event) => setSelected(event.target.files?.[0] ?? null)}
        />
        <Button
          disabled={!selected || upload.isPending}
          onClick={() => {
            if (!selected) return;
            upload.mutate(
              { file: selected },
              {
                onSuccess: () => {
                  setSelected(null);
                  if (inputRef.current) inputRef.current.value = "";
                },
              },
            );
          }}
        >
          {upload.isPending ? "Uploading…" : "Upload"}
        </Button>
      </div>

      {upload.isError && (
        <Banner tone="danger" title="The file was not accepted" className="mt-3">
          {upload.error instanceof Error
            ? upload.error.message
            : "Try again, or enter the result manually."}
        </Banner>
      )}

      {upload.isSuccess && (
        <Banner
          tone={upload.data.duplicate ? "info" : "success"}
          title={upload.data.duplicate ? "Already received" : "Report received"}
          className="mt-3"
        >
          <p>{upload.data.message}</p>
          {/* `skipped` is not `clean`. Saying so here is the difference
              between an honest record and a false assurance. */}
          {upload.data.virus_scan === "skipped" && (
            <p className="mt-1 text-xs">
              No virus scanner is configured on this server, so this file was
              not scanned.
            </p>
          )}
          <p className="mt-1">
            <Link
              className="underline"
              to={`/documents/${upload.data.document_id}`}
            >
              Open the document
            </Link>
          </p>
        </Banner>
      )}
    </section>
  );
}

export function DocumentQueuePage() {
  const queue = useReviewQueue();

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-ink">Documents</h1>
        <p className="mt-1 text-sm text-ink-body">
          Uploaded reports the system could not read on its own. Open one to
          enter it by hand with the scanned page beside the form.
        </p>
      </header>

      <UploadPanel />

      <section>
        <h2 className="text-sm font-semibold text-ink">
          Waiting for a person
        </h2>

        {queue.isLoading && <Loading label="Loading the review queue" />}
        {queue.isError && (
          <ErrorState
            error={queue.error}
            onRetry={() => void queue.refetch()}
            fallbackTitle="The review queue could not be loaded"
          />
        )}

        {queue.data && queue.data.documents.length === 0 && (
          <Banner tone="success" title="Nothing waiting" className="mt-3">
            Every report received so far was read successfully.
          </Banner>
        )}

        {queue.data && queue.data.documents.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-line text-left">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Received
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    File
                  </th>
                  <th scope="col" className="w-36 py-2 pr-3 font-medium">
                    Status
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    What went wrong
                  </th>
                </tr>
              </thead>
              <tbody>
                {queue.data.documents.map((row) => (
                  <tr key={row.id} className="border-b border-line align-top">
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {formatWhen(row.received_at)}
                    </td>
                    <td className="py-2 pr-3">
                      <Link
                        className="font-medium text-brand-text underline"
                        to={`/documents/${row.id}`}
                      >
                        {row.original_filename ?? "Untitled report"}
                      </Link>
                      <span className="block text-xs text-ink-muted">
                        {formatBytes(row.size_bytes)}
                        {row.page_count ? ` · ${row.page_count} pages` : ""}
                        {` · via ${row.source_channel.replace("_", " ")}`}
                      </span>
                    </td>
                    <td className="py-2 pr-3">
                      <StatusBadge status={row.status} />
                    </td>
                    <td className="py-2 pr-3 text-ink-body">
                      {row.error_text ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
