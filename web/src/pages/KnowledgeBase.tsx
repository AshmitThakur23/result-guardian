/**
 * Phase 8.1 — the knowledge base, and the approval that makes it visible.
 *
 * Until this screen existed there was no way to put guidance into the product
 * except by calling the API by hand, which meant the Explain panel could only
 * ever answer questions about whatever a seed script happened to load. The
 * hooks had been written (`useKbDocuments`) and never wired to a page.
 *
 * ## Two steps, on purpose
 *
 * Adding a document and approving it are **separate actions**, because a
 * document becomes retrievable when somebody approves it, never because
 * somebody uploaded it. 8.1: *"an unapproved guideline must not reach a
 * doctor"* — which is only enforceable if approval is an act with a name
 * against it. So a freshly added document sits here marked "Not retrievable"
 * until a person presses Approve, and `approved_by` records who.
 *
 * ## Why the identifier warnings are shown and not obeyed
 *
 * Ingestion scans for anything that looks like it belongs to a person — MRNs,
 * phone numbers, dates of birth. It **returns** what it found instead of
 * refusing, because a guideline may legitimately contain the word "Patient:"
 * in a worked example. The judgement belongs to the person about to approve
 * it, and this screen is where they get to make it, with the offending lines
 * in front of them.
 *
 * ## What this screen is not
 *
 * It is not a document store for patient reports — those live in Phase 6's
 * queue. Nothing here is about one patient, and the ingest scan exists to keep
 * it that way.
 */

import { useState } from "react";

import {
  useApproveKbDocument,
  useIngestKbDocument,
  useKbDocuments,
} from "../api/queries8";
import { KB_DOC_TYPES, KB_PUBLISHERS } from "../api/types8";
import type { IngestResult, KbDocument } from "../api/types8";
import { Badge } from "../components/ui/Badge";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { ErrorState, Loading, Spinner } from "../components/ui/States";

const LABELS: Record<string, string> = {
  hospital: "This hospital",
  who: "WHO",
  icmr: "ICMR",
  nlem: "NLEM",
  other: "Other",
  antibiotic_policy: "Antibiotic policy",
  guideline: "Guideline",
  antibiogram: "Antibiogram",
  protocol: "Protocol",
  sop: "SOP",
  formulary: "Formulary",
};

function label(value: string): string {
  return LABELS[value] ?? value;
}

function DocumentRow({ document }: { document: KbDocument }) {
  const approve = useApproveKbDocument();

  return (
    <li className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{document.title}</p>
        <p className="mt-0.5 text-xs text-ink-muted">
          {label(document.publisher)} · {label(document.doc_type)} · v
          {document.version} · {document.chunks} passage
          {document.chunks === 1 ? "" : "s"}
        </p>
      </div>

      {document.approved ? (
        <Badge tone="normal" dot>
          Retrievable
        </Badge>
      ) : (
        // `followup`, not `critical`. An unapproved document is the correct
        // resting state for something just uploaded, not a fault.
        <Badge tone="followup">Not retrievable</Badge>
      )}

      {!document.approved ? (
        <Button
          size="sm"
          disabled={approve.isPending}
          onClick={() => approve.mutate(document.id)}
        >
          {approve.isPending ? <Spinner /> : null}
          Approve
        </Button>
      ) : null}
    </li>
  );
}

function AddForm() {
  const ingest = useIngestKbDocument();
  const [title, setTitle] = useState("");
  const [publisher, setPublisher] = useState<string>("hospital");
  const [docType, setDocType] = useState<string>("antibiotic_policy");
  const [body, setBody] = useState("");
  const [sourceRef, setSourceRef] = useState("");

  const result: IngestResult | undefined = ingest.data;
  // Mirrors the API's own `min_length`, so the button is disabled for the same
  // reason the server would refuse rather than for a different one.
  const ready = title.trim().length >= 3 && body.trim().length >= 50;

  return (
    <section className="rounded-lg border border-line bg-surface">
      <header className="border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Add guidance</h2>
        <p className="mt-0.5 max-w-prose text-sm text-ink-muted">
          Paste the text of a policy, guideline or antibiogram. It is stored{" "}
          <strong className="font-medium text-ink-body">unapproved</strong> and
          stays invisible to the Explain panel until somebody approves it below.
        </p>
      </header>

      <div className="grid gap-3 px-4 py-4 sm:grid-cols-2">
        <label className="block sm:col-span-2">
          <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
            Title
          </span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Antibiotic Policy 2026 — urinary tract infection"
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink"
          />
        </label>

        <label className="block">
          <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
            Publisher
          </span>
          <select
            value={publisher}
            onChange={(event) => setPublisher(event.target.value)}
            className="mt-1 block w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink"
          >
            {KB_PUBLISHERS.map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
            Kind
          </span>
          <select
            value={docType}
            onChange={(event) => setDocType(event.target.value)}
            className="mt-1 block w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink"
          >
            {KB_DOC_TYPES.map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </label>

        <label className="block sm:col-span-2">
          <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
            Where it came from{" "}
            <span className="font-normal normal-case text-ink-muted">
              (optional, but a citation with no provenance is worth little)
            </span>
          </span>
          <input
            value={sourceRef}
            onChange={(event) => setSourceRef(event.target.value)}
            placeholder="Hospital Antimicrobial Policy v4, approved by the Infection Control Committee"
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink"
          />
        </label>

        <label className="block sm:col-span-2">
          <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
            Text
          </span>
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={10}
            placeholder={
              "Paste the guidance here.\n\nKeep the section headings — the " +
              "chunker uses them, and a citation can then name the section it " +
              "came from instead of just the document."
            }
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 font-mono text-xs leading-relaxed text-ink"
          />
          <span className="mt-1 block text-xs text-ink-muted">
            {body.trim().length} characters. At least 50 are needed.
          </span>
        </label>
      </div>

      <footer className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
        <Button
          disabled={!ready || ingest.isPending}
          onClick={() =>
            ingest.mutate({
              title: title.trim(),
              publisher,
              doc_type: docType,
              document_text: body,
              source_ref: sourceRef.trim() || null,
            })
          }
        >
          {ingest.isPending ? <Spinner /> : null}
          Add, unapproved
        </Button>
        {!ready ? (
          <span className="text-xs text-ink-muted">
            A title of 3 characters and 50 characters of text are the minimum.
          </span>
        ) : null}
      </footer>

      {ingest.isError ? (
        <div className="px-4 pb-4">
          <Banner tone="warning" title="It was not stored">
            The document could not be added. Nothing was saved, so nothing is
            half-loaded — correct the fields and try again.
          </Banner>
        </div>
      ) : null}

      {result ? (
        <div className="space-y-3 px-4 pb-4">
          <Banner tone="info" title={`Stored as ${result.chunks} passage(s)`}>
            <p>{result.message}</p>
          </Banner>

          {/* Shown, never obeyed. The person approving decides. */}
          {result.identifier_warnings.length > 0 ? (
            <Banner
              tone="warning"
              title={`${result.identifier_warnings.length} line(s) look like they mention a person`}
            >
              <p>
                The knowledge base must not hold patient data. Read these before
                approving — a guideline may legitimately say "Patient:" in an
                example, and only you can tell the difference.
              </p>
              <ul className="mt-2 space-y-1">
                {result.identifier_warnings.map((warning, index) => (
                  <li key={index} className="font-mono text-xs">
                    <span className="uppercase text-ink-muted">
                      {warning.kind}
                    </span>{" "}
                    {warning.excerpt}
                  </li>
                ))}
              </ul>
            </Banner>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function KnowledgeBasePage() {
  const documents = useKbDocuments();

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
      <header>
        <h1 className="text-lg font-semibold text-ink">Knowledge base</h1>
        <p className="mt-1 max-w-prose text-sm text-ink-muted">
          The approved guidance the Explain panel is allowed to quote.{" "}
          <strong className="font-medium text-ink-body">
            Nothing here is about a patient
          </strong>{" "}
          — this is policy, and every quotation a clinician is shown must be
          traceable to a document on this page.
        </p>
      </header>

      <AddForm />

      <section className="rounded-lg border border-line bg-surface">
        <header className="border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">
            What the knowledge base holds
          </h2>
        </header>

        {documents.isPending ? (
          <div className="px-4 py-6">
            <Loading />
          </div>
        ) : documents.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              error={documents.error}
              onRetry={() => documents.refetch()}
            />
          </div>
        ) : documents.data && documents.data.documents.length > 0 ? (
          <ul>
            {documents.data.documents.map((document) => (
              <DocumentRow key={document.id} document={document} />
            ))}
          </ul>
        ) : (
          <p className="px-4 py-6 text-sm text-ink-muted">
            Nothing yet. Until a document is added <em>and approved</em>, the
            Explain panel will correctly say it has no guidance to work from.
          </p>
        )}
      </section>
    </div>
  );
}

