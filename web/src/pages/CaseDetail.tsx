/**
 * The case page. Phase 5.2 / 5.3.
 *
 *     patient & encounter header
 *     the result, rendered as a table (analytes), grid (sensitivities) or
 *     text (narrative)
 *     abnormal values highlighted with the reference range
 *     rule engine output **in plain language**
 *     full timeline from `case_events`
 *     action bar: Acknowledge / Reassign / Add note
 *
 * **The plain-language block goes above the raw result**, because that is the
 * order a doctor at 3am needs: what is wrong, then the numbers behind it. The
 * reason codes are kept alongside rather than hidden — a clinician who wants
 * to know exactly which rule fired should not have to open the audit trail.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useAddNote, useCaseDetail, useCloseCase, useReopenCase } from "../api/queries5";
import type { AnalyteRow, CaseDetail, OrganismRow, RuleExplanation } from "../api/types5";
import {
  CLOSURE_REASONS,
  CLOSURE_REASON_LABELS,
  CLOSURE_REASON_PROMPTS,
} from "../api/types5";
import type { ClosureReason } from "../api/types5";
import { ExplainPanel } from "../components/ExplainPanel";
import { SeverityBadge } from "../components/SeverityBadge";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { ErrorState, Loading } from "../components/ui/States";
import { formatAge, formatCountdown } from "../lib/countdown";
import { cn } from "../lib/cn";

const MIN_NOTE = 10;

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const { data, isPending, isError, error, refetch } = useCaseDetail(caseId);

  if (isPending) return <Loading label="Loading the case…" />;
  if (isError || !data) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  return (
    <article className="space-y-6">
      <CaseHeader detail={data} />
      <Explanations explanations={data.explanations} />
      <ResultBlock detail={data} />
      {/* Phase 8. Placed under the result and above the actions: a clinician
          reads what happened, may ask why it matters, and then decides. Putting
          it above the result would let generated prose frame the finding before
          the finding has been read. */}
      <ExplainPanel caseId={data.case_id} query={explainQuery(data)} />
      <ActionBar detail={data} />
      <Timeline detail={data} />
    </article>
  );
}

/**
 * The retrieval query, built from **structured fields only**.
 *
 * 8.3 forbids the raw report text reaching the query: a report is full of the
 * patient's name, the hospital's letterhead and the lab's phone number, and
 * searching a guideline store with that finds documents that share a word with
 * an address. It also keeps identifiers out of a string that may later be
 * logged.
 *
 * Organism and resistance first, because those are what the antibiotic policy
 * is indexed on; the analyte name is the fallback for a chemistry result.
 */
function explainQuery(detail: CaseDetail): string {
  const parts: string[] = [];

  const organism = detail.organisms[0];
  if (organism) {
    parts.push(organism.organism);
    // `interpretation`, not `result` -- and the antibiotic name is nullable,
    // because a lab can report a sensitivity row with the drug column blank.
    const resistant = organism.sensitivities.find(
      (cell) => cell.interpretation === "R" && cell.antibiotic,
    );
    if (resistant?.antibiotic) parts.push(resistant.antibiotic, "resistant");
    if (organism.specimen_type) parts.push(organism.specimen_type);
  }

  if (parts.length === 0 && detail.analytes.length > 0) {
    parts.push(String(detail.analytes[0].test_name ?? ""));
  }
  if (parts.length === 0) parts.push(String(detail.order.test_name ?? ""));

  return parts.filter(Boolean).join(" ").slice(0, 300);
}

function text(value: unknown, fallback = "—"): string {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function CaseHeader({ detail }: { detail: CaseDetail }) {
  const countdown = formatCountdown(
    detail.next_escalation_at
      ? Math.round(
          (new Date(detail.next_escalation_at).getTime() - Date.now()) / 1000,
        )
      : null,
  );

  return (
    <header className="rounded-md border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">
            {text(detail.patient.full_name)}
          </h1>
          <p className="text-sm text-ink-body">
            MRN {text(detail.patient.mrn)} · {text(detail.encounter.department_name)}
            {detail.encounter.discharged_at
              ? ` · discharged ${new Date(
                  String(detail.encounter.discharged_at),
                ).toLocaleDateString()}`
              : null}
          </p>
          <p className="mt-1 text-sm text-ink-body">{text(detail.order.test_name)}</p>
        </div>

        <div className="ml-auto flex flex-col items-end gap-2">
          {detail.severity ? <SeverityBadge severity={detail.severity} /> : null}
          <span className="text-xs text-ink-body">
            {detail.flagged_at
              ? formatAge(
                  Math.round((Date.now() - new Date(detail.flagged_at).getTime()) / 1000),
                )
              : "Not yet flagged"}
          </span>
          {detail.escalation_level !== null ? (
            <span className="text-xs text-ink-body">
              Escalation rung {detail.escalation_level}
            </span>
          ) : null}
          {countdown ? (
            <span
              className={cn(
                "text-xs",
                countdown.overdue ? "font-semibold text-critical-text" : "text-ink-body",
              )}
            >
              Next escalation {countdown.label}
            </span>
          ) : null}
        </div>
      </div>

      {detail.closed_at ? (
        <Banner tone="success" title="This case is closed" className="mt-4">
          <p>
            Closed as <strong>{text(detail.closure_reason)}</strong>
            {detail.closure_note ? `: ${detail.closure_note}` : null}
          </p>
        </Banner>
      ) : null}

      {detail.reopened_count > 0 ? (
        <p className="mt-2 text-sm text-followup-text">
          This case has been reopened {detail.reopened_count}{" "}
          {detail.reopened_count === 1 ? "time" : "times"}.
        </p>
      ) : null}
    </header>
  );
}

function Explanations({ explanations }: { explanations: RuleExplanation[] }) {
  if (explanations.length === 0) {
    return (
      <section className="rounded-md border border-dashed border-line bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">Why this is flagged</h2>
        <p className="mt-1 text-sm text-ink-body">
          No result has been classified yet. The case is being tracked and will
          be classified as soon as a result arrives.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-md border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">Why this is flagged</h2>
      <ul className="mt-3 space-y-3">
        {explanations.map((item, index) => (
          <li key={`${item.rule_id}-${item.reason_code}-${index}`}>
            <p
              className={cn(
                "text-sm",
                item.severity === "critical"
                  ? "font-semibold text-critical-text"
                  : "text-ink",
              )}
            >
              {item.headline}
            </p>
            {item.detail ? (
              <p className="mt-0.5 text-sm text-ink-body">{item.detail}</p>
            ) : null}
            {/* The code, kept visible. A clinician who wants to know exactly
                which rule fired should not have to open the audit trail. */}
            <p className="mt-0.5 font-mono text-xs text-ink-muted">
              rule {item.rule_id} · {item.reason_code}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ResultBlock({ detail }: { detail: CaseDetail }) {
  const hasContent =
    detail.analytes.length > 0 ||
    detail.organisms.length > 0 ||
    detail.narratives.length > 0;

  if (!detail.result) {
    return (
      <section className="rounded-md border border-dashed border-line bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">Result</h2>
        <p className="mt-1 text-sm text-ink-body">
          No result has arrived yet. The case stays open and escalates until one
          does — that is the point.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-4 rounded-md border border-line bg-surface p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-sm font-semibold text-ink">Result</h2>
        <span className="text-xs text-ink-muted">
          {text(detail.result.report_status)} · received{" "}
          {new Date(String(detail.result.received_at)).toLocaleString()}
        </span>
      </div>

      {detail.analytes.length > 0 ? <AnalyteTable rows={detail.analytes} /> : null}
      {detail.organisms.map((organism) => (
        <SensitivityGrid key={organism.organism} organism={organism} />
      ))}
      {detail.narratives.map((narrative) => (
        <div key={narrative.section}>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-body">
            {narrative.section}
          </h3>
          <p className="mt-1 whitespace-pre-wrap text-sm text-ink">
            {narrative.text}
          </p>
        </div>
      ))}

      {!hasContent ? (
        <p className="text-sm text-ink-body">
          The report arrived with no structured content. Open the source report.
        </p>
      ) : null}
    </section>
  );
}

function AnalyteTable({ rows }: { rows: AnalyteRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-line text-xs uppercase tracking-wide text-ink-body">
          <tr>
            <th scope="col" className="py-2 pr-3">Test</th>
            <th scope="col" className="py-2 pr-3">Value</th>
            <th scope="col" className="py-2 pr-3">Reference</th>
            <th scope="col" className="py-2">Flag</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.seq} className="border-b border-slate-100 last:border-0">
              <td className="py-2 pr-3 text-ink">{row.test_name}</td>
              <td
                className={cn(
                  "py-2 pr-3",
                  // Colour is never the only signal -- the Flag column
                  // carries the word too, for a colour-blind reader and for
                  // a printed report.
                  row.abnormal ? "font-semibold text-critical-text" : "text-ink",
                )}
              >
                {row.value ?? "—"} {row.unit ?? ""}
              </td>
              <td className="py-2 pr-3 text-ink-body">
                {row.ref_text ??
                  (row.ref_low || row.ref_high
                    ? `${row.ref_low ?? "−∞"} – ${row.ref_high ?? "∞"}`
                    : "Not given")}
              </td>
              <td className="py-2 text-sm">
                {row.abnormal ? (
                  <span className="font-medium text-critical-text">
                    {row.abnormal_direction === "high"
                      ? "High"
                      : row.abnormal_direction === "low"
                        ? "Low"
                        : "Abnormal"}
                  </span>
                ) : (
                  <span className="text-ink-muted">Normal</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const INTERPRETATION_LABELS: Record<string, string> = {
  S: "Sensitive",
  I: "Intermediate",
  R: "Resistant",
};

function SensitivityGrid({ organism }: { organism: OrganismRow }) {
  return (
    <div>
      <h3 className="text-sm font-medium text-ink">
        {organism.organism}
        {organism.colony_count ? (
          <span className="ml-2 text-xs font-normal text-ink-body">
            {organism.colony_count}
          </span>
        ) : null}
        {organism.specimen_type ? (
          <span className="ml-2 text-xs font-normal text-ink-muted">
            ({organism.specimen_type})
          </span>
        ) : null}
      </h3>

      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs uppercase tracking-wide text-ink-body">
            <tr>
              <th scope="col" className="py-2 pr-3">Antibiotic</th>
              <th scope="col" className="py-2 pr-3">Result</th>
              <th scope="col" className="py-2">MIC</th>
            </tr>
          </thead>
          <tbody>
            {organism.sensitivities.map((cell) => (
              <tr
                key={cell.antibiotic ?? ""}
                className="border-b border-slate-100 last:border-0"
              >
                <td className="py-2 pr-3 text-ink">{cell.antibiotic ?? "—"}</td>
                <td
                  className={cn(
                    "py-2 pr-3",
                    cell.interpretation === "R"
                      ? "font-semibold text-critical-text"
                      : cell.interpretation === "I"
                        ? "font-medium text-followup-text"
                        : "text-normal-text",
                  )}
                >
                  {INTERPRETATION_LABELS[cell.interpretation ?? ""] ??
                    cell.interpretation ??
                    "—"}
                </td>
                <td className="py-2 text-ink-body">{cell.mic ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ActionBar({ detail }: { detail: CaseDetail }) {
  const [mode, setMode] = useState<"none" | "close" | "note" | "reopen">("none");

  return (
    <section className="rounded-md border border-line bg-surface p-4">
      <div className="flex flex-wrap gap-2">
        {detail.can_acknowledge ? (
          <Button onClick={() => setMode(mode === "close" ? "none" : "close")}>
            Acknowledge and close
          </Button>
        ) : (
          <Button
            variant="secondary"
            onClick={() => setMode(mode === "reopen" ? "none" : "reopen")}
          >
            Reopen
          </Button>
        )}
        <Button variant="secondary" onClick={() => setMode(mode === "note" ? "none" : "note")}>
          Add a note
        </Button>
        <Link
          to={`/patients/${text(detail.patient.id, "")}`}
          className="inline-flex items-center rounded-md px-4 py-2 text-sm font-medium text-ink-body hover:bg-surface-sunken"
        >
          Open the patient
        </Link>
      </div>

      {mode === "close" ? (
        <CloseForm detail={detail} onDone={() => setMode("none")} />
      ) : null}
      {mode === "note" ? <NoteForm detail={detail} onDone={() => setMode("none")} /> : null}
      {mode === "reopen" ? (
        <ReopenForm detail={detail} onDone={() => setMode("none")} />
      ) : null}
    </section>
  );
}

function CloseForm({ detail, onDone }: { detail: CaseDetail; onDone: () => void }) {
  const [reason, setReason] = useState<ClosureReason>(CLOSURE_REASONS[0]);
  const [note, setNote] = useState("");
  const [duplicateOf, setDuplicateOf] = useState("");
  const mutation = useCloseCase(detail.case_id);

  const needsDuplicate = reason === "duplicate_report";
  const valid = note.trim().length >= MIN_NOTE && (!needsDuplicate || duplicateOf !== "");

  return (
    <form
      className="mt-4 space-y-3 border-t border-line pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate(
          {
            closure_reason: reason,
            closure_note: note,
            duplicate_of_case_id: needsDuplicate ? duplicateOf : null,
          },
          { onSuccess: onDone },
        );
      }}
    >
      {detail.severity === "critical" ? (
        <Banner tone="warning" title="This is a critical result">
          <p>
            Critical results are closed one at a time, never in bulk. Confirm
            what was done before closing.
          </p>
        </Banner>
      ) : null}

      <label className="block">
        <span className="text-sm font-medium text-ink">Closure reason</span>
        <select
          value={reason}
          onChange={(event) => setReason(event.target.value as ClosureReason)}
          className="mt-1 block rounded-md border border-line px-3 py-2 text-sm"
        >
          {CLOSURE_REASONS.map((value) => (
            <option key={value} value={value}>
              {CLOSURE_REASON_LABELS[value]}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="text-sm font-medium text-ink">
          {CLOSURE_REASON_PROMPTS[reason]}
        </span>
        <textarea
          required
          minLength={MIN_NOTE}
          rows={3}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
        />
        <span className="mt-1 block text-xs text-ink-muted">
          At least {MIN_NOTE} characters. This is what an auditor reads.
        </span>
      </label>

      {needsDuplicate ? (
        <label className="block">
          <span className="text-sm font-medium text-ink">Original case ID</span>
          <input
            required
            value={duplicateOf}
            onChange={(event) => setDuplicateOf(event.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 font-mono text-sm"
          />
        </label>
      ) : null}

      {mutation.isError ? <ErrorState error={mutation.error} /> : null}

      <div className="flex gap-2">
        <Button type="submit" disabled={!valid || mutation.isPending}>
          {mutation.isPending ? "Closing…" : "Close this case"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function NoteForm({ detail, onDone }: { detail: CaseDetail; onDone: () => void }) {
  const [note, setNote] = useState("");
  const mutation = useAddNote(detail.case_id);

  return (
    <form
      className="mt-4 space-y-3 border-t border-line pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate({ note }, { onSuccess: onDone });
      }}
    >
      <label className="block">
        <span className="text-sm font-medium text-ink">Note</span>
        <textarea
          required
          rows={3}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
        />
      </label>
      {mutation.isError ? <ErrorState error={mutation.error} /> : null}
      <div className="flex gap-2">
        <Button type="submit" disabled={!note.trim() || mutation.isPending}>
          {mutation.isPending ? "Saving…" : "Add the note"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ReopenForm({ detail, onDone }: { detail: CaseDetail; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const mutation = useReopenCase(detail.case_id);

  return (
    <form
      className="mt-4 space-y-3 border-t border-line pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate({ reason }, { onSuccess: onDone });
      }}
    >
      <label className="block">
        <span className="text-sm font-medium text-ink">
          Why is this being reopened?
        </span>
        <textarea
          required
          minLength={MIN_NOTE}
          rows={3}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
        />
        <span className="mt-1 block text-xs text-ink-muted">
          The original closure stays in the history. Reopening adds to it.
        </span>
      </label>
      {mutation.isError ? <ErrorState error={mutation.error} /> : null}
      <div className="flex gap-2">
        <Button type="submit" disabled={reason.trim().length < MIN_NOTE || mutation.isPending}>
          {mutation.isPending ? "Reopening…" : "Reopen this case"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

const EVENT_LABELS: Record<string, string> = {
  case_opened: "Case opened",
  result_received: "Result received",
  case_classified: "Classified by the rule engine",
  case_flagged: "Flagged",
  case_acknowledged: "Acknowledged",
  case_closed: "Closed",
  case_reopened: "Reopened",
  case_note_added: "Note added",
  case_reassigned: "Reassigned",
  escalation_rung_fired: "Escalation rung fired",
  ladder_cancelled: "Escalation cancelled",
  timers_cancelled: "Timers cancelled",
};

function Timeline({ detail }: { detail: CaseDetail }) {
  if (detail.timeline.length === 0) {
    return null;
  }

  return (
    <section className="rounded-md border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">History</h2>
      <ol className="mt-3 space-y-3">
        {detail.timeline.map((event, index) => (
          <li key={`${event.occurred_at}-${index}`} className="flex gap-3 text-sm">
            <time
              dateTime={event.occurred_at}
              className="w-40 shrink-0 text-xs text-ink-muted"
            >
              {new Date(event.occurred_at).toLocaleString()}
            </time>
            <div>
              <p className="text-ink">
                {EVENT_LABELS[event.event_type] ?? event.event_type}
                {event.actor_name ? (
                  <span className="text-ink-muted"> · {event.actor_name}</span>
                ) : (
                  <span className="text-ink-muted"> · system</span>
                )}
              </p>
              {typeof event.payload.note === "string" ? (
                <p className="text-ink-body">{event.payload.note}</p>
              ) : null}
              {typeof event.payload.reason === "string" ? (
                <p className="text-ink-body">{event.payload.reason}</p>
              ) : null}
              {typeof event.payload.closure_reason === "string" ? (
                <p className="text-ink-body">
                  Reason: {event.payload.closure_reason}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
