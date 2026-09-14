/**
 * The doctor's worklist. Phase 5.2.
 *
 *     Default view: my open flags, sorted **CRITICAL first, then oldest**
 *     Rows show: patient name + MRN, test, severity chip, age of flag,
 *     escalation rung, next escalation time (countdown)
 *     Filters: severity, department, date range, state
 *
 * **The server does the sorting.** It would be easy to re-sort the page in
 * the browser and quietly disagree with the cursor's ordering — at which
 * point page 2 contains rows that belong on page 1 and a case can be missed.
 * The rows are rendered in the order they arrive, full stop.
 *
 * Bulk close is offered, and **CRITICAL cases are excluded from it by the
 * server**. The checkbox is disabled here too, with the reason stated, so the
 * refusal is understood rather than discovered as a 409.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useBulkClose, useWorklist } from "../api/queries5";
import type { WorklistFilters, WorklistRow } from "../api/types5";
import { CLOSURE_REASONS, CLOSURE_REASON_LABELS } from "../api/types5";
import { useAuth } from "../auth/AuthProvider";
import { SeverityBadge } from "../components/SeverityBadge";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { Empty, ErrorState, Loading } from "../components/ui/States";
import { formatAge, formatCountdown } from "../lib/countdown";
import { cn } from "../lib/cn";

const SEVERITY_OPTIONS = [
  { value: "critical", label: "Critical" },
  { value: "follow_up", label: "Needs follow-up" },
  { value: "normal", label: "Normal" },
];

export function WorklistPage() {
  const { user } = useAuth();
  const [filters, setFilters] = useState<WorklistFilters>({ mine_only: true });
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isPending, isError, error, refetch, isFetching } = useWorklist(
    filters,
    cursor,
    { withTotal: true },
  );

  const rows = data?.rows ?? [];
  const selectable = useMemo(
    () => rows.filter((row) => row.severity !== "critical"),
    [rows],
  );

  function update(patch: Partial<WorklistFilters>) {
    // Any filter change invalidates the cursor: it encodes a position in the
    // *previous* result set, and reusing it would skip rows.
    setCursor(null);
    setSelected(new Set());
    setFilters((current) => ({ ...current, ...patch }));
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold text-ink">
          {filters.mine_only ? "My open flags" : "Department flags"}
        </h1>
        {data?.total_open != null ? (
          <span className="text-sm text-ink-body">{data.total_open} open</span>
        ) : null}
        <span className="ml-auto text-xs text-ink-muted">
          {isFetching ? "Refreshing…" : "Refreshes every 30 seconds"}
        </span>
      </header>

      <Filters filters={filters} onChange={update} />

      {selected.size > 0 ? (
        <BulkCloseBar
          caseIds={[...selected]}
          onDone={() => {
            setSelected(new Set());
            void refetch();
          }}
          onCancel={() => setSelected(new Set())}
        />
      ) : null}

      {isPending ? <Loading label="Loading your worklist…" /> : null}
      {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : null}

      {!isPending && !isError && rows.length === 0 ? (
        <Empty title="Nothing is waiting for you">
          {filters.mine_only
            ? "No open flags are assigned to you. Switch to your department's view to see the rest."
            : "No open flags match these filters."}
        </Empty>
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Open flags, most critical first, then oldest
            </caption>
            <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
              <tr>
                <th scope="col" className="w-10 px-3 py-2">
                  <span className="sr-only">Select</span>
                </th>
                <th scope="col" className="px-3 py-2">Patient</th>
                <th scope="col" className="px-3 py-2">Test</th>
                <th scope="col" className="px-3 py-2">Severity</th>
                <th scope="col" className="px-3 py-2">Age</th>
                <th scope="col" className="px-3 py-2">Rung</th>
                <th scope="col" className="px-3 py-2">Next escalation</th>
                <th scope="col" className="px-3 py-2">Owner</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Row
                  key={row.case_id}
                  row={row}
                  checked={selected.has(row.case_id)}
                  onToggle={(on) =>
                    setSelected((current) => {
                      const next = new Set(current);
                      if (on) next.add(row.case_id);
                      else next.delete(row.case_id);
                      return next;
                    })
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="flex items-center gap-3">
        {cursor ? (
          <Button variant="secondary" onClick={() => setCursor(null)}>
            Back to the first page
          </Button>
        ) : null}
        {data?.next_cursor ? (
          <Button
            variant="secondary"
            onClick={() => {
              setSelected(new Set());
              setCursor(data.next_cursor);
            }}
          >
            Next page
          </Button>
        ) : null}
        {selectable.length > 0 && selected.size === 0 ? (
          <Button
            variant="ghost"
            onClick={() => setSelected(new Set(selectable.map((r) => r.case_id)))}
          >
            Select all non-critical on this page
          </Button>
        ) : null}
      </div>

      {user?.role === "auditor" ? (
        <p className="text-xs text-ink-muted">
          You are signed in as an auditor: this view is read-only.
        </p>
      ) : null}
    </div>
  );
}

function Row({
  row,
  checked,
  onToggle,
}: {
  row: WorklistRow;
  checked: boolean;
  onToggle: (on: boolean) => void;
}) {
  const countdown = formatCountdown(row.seconds_to_next_escalation);
  const isCritical = row.severity === "critical";

  return (
    <tr className={cn("border-b border-slate-100 last:border-0", isCritical && "bg-critical-subtle/40")}>
      <td className="px-3 py-2 align-top">
        <input
          type="checkbox"
          checked={checked}
          disabled={isCritical}
          aria-label={
            isCritical
              ? `${row.patient_name}: critical cases must be closed individually`
              : `Select ${row.patient_name}`
          }
          title={
            isCritical
              ? "Critical results must be opened and closed one at a time."
              : undefined
          }
          onChange={(event) => onToggle(event.target.checked)}
          className="mt-1 h-4 w-4"
        />
      </td>
      <td className="px-3 py-2 align-top">
        <Link
          to={`/cases/${row.case_id}`}
          className="font-medium text-brand-text underline-offset-2 hover:underline"
        >
          {row.patient_name}
        </Link>
        <div className="text-xs text-ink-muted">{row.mrn}</div>
        {row.reopened_count > 0 ? (
          <div className="text-xs font-medium text-followup-text">
            Reopened {row.reopened_count}×
          </div>
        ) : null}
      </td>
      <td className="px-3 py-2 align-top">
        <div>{row.test_name}</div>
        <div className="max-w-md text-xs text-ink-body">{row.summary}</div>
      </td>
      <td className="px-3 py-2 align-top">
        {row.severity ? (
          <SeverityBadge severity={row.severity} />
        ) : (
          <span className="text-xs text-ink-muted">Not yet classified</span>
        )}
      </td>
      <td className="px-3 py-2 align-top text-ink-body">{formatAge(row.age_seconds)}</td>
      <td className="px-3 py-2 align-top text-ink-body">
        {row.escalation_level === null ? "—" : `Rung ${row.escalation_level}`}
      </td>
      <td className="px-3 py-2 align-top">
        {countdown ? (
          <span
            className={cn(
              "text-sm",
              countdown.overdue ? "font-semibold text-critical-text" : "text-ink-body",
            )}
          >
            {countdown.label}
          </span>
        ) : (
          <span className="text-sm text-ink-muted">None scheduled</span>
        )}
      </td>
      <td className="px-3 py-2 align-top text-ink-body">{row.owner_name ?? "Unassigned"}</td>
    </tr>
  );
}

function BulkCloseBar({
  caseIds,
  onDone,
  onCancel,
}: {
  caseIds: string[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState<string>(CLOSURE_REASONS[0]);
  const [note, setNote] = useState("");
  const mutation = useBulkClose();

  const critical = mutation.error as { problem?: Record<string, unknown> } | null;
  const refusedIds = Array.isArray(critical?.problem?.critical_case_ids)
    ? (critical.problem.critical_case_ids as string[])
    : [];

  return (
    <div className="rounded-md border border-line bg-surface p-4">
      <p className="text-sm font-medium text-ink">
        Close {caseIds.length} {caseIds.length === 1 ? "case" : "cases"}
      </p>

      {refusedIds.length > 0 ? (
        <Banner tone="danger" title="Critical cases must be closed individually" className="mt-3">
          <p>
            {refusedIds.length} of the selected cases are critical. Open each one
            and close it on its own — this is deliberate.
          </p>
        </Banner>
      ) : null}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="text-sm font-medium text-ink">Reason</span>
          <select
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 block rounded-md border border-line px-3 py-2 text-sm"
          >
            {CLOSURE_REASONS.map((value) => (
              <option key={value} value={value}>
                {CLOSURE_REASON_LABELS[value]}
              </option>
            ))}
          </select>
        </label>

        <label className="block flex-1">
          <span className="text-sm font-medium text-ink">Note (required)</span>
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            minLength={10}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
          />
        </label>

        <Button
          disabled={mutation.isPending || note.trim().length < 10}
          onClick={() =>
            mutation.mutate(
              { case_ids: caseIds, closure_reason: reason, closure_note: note },
              { onSuccess: onDone },
            )
          }
        >
          {mutation.isPending ? "Closing…" : "Close selected"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>

      {mutation.isError && refusedIds.length === 0 ? (
        <ErrorState error={mutation.error} />
      ) : null}
    </div>
  );
}

/**
 * How far back to look, in days. `null` means "do not filter at all".
 *
 * Offered as a fixed list rather than two date pickers because the question a
 * ward actually asks is "what came in this week?", and a pair of calendars is
 * four interactions to answer it. The endpoint takes arbitrary instants, so a
 * custom range remains possible if anyone ever needs one.
 */
const PERIODS: { value: string; label: string; days: number | null }[] = [
  { value: "any", label: "Any time", days: null },
  { value: "7", label: "This week", days: 7 },
  { value: "14", label: "Last 2 weeks", days: 14 },
  { value: "30", label: "Last month", days: 30 },
  { value: "90", label: "Last 3 months", days: 90 },
];

/** The ISO instant `days` ago, or `undefined` to clear the filter. */
function sinceFor(value: string): string | undefined {
  const period = PERIODS.find((option) => option.value === value);
  if (!period?.days) return undefined;
  return new Date(Date.now() - period.days * 86_400_000).toISOString();
}

/**
 * Which option a stored `opened_from` corresponds to.
 *
 * Matched on the nearest boundary rather than on equality: the stored value is
 * an instant computed when the user chose, so by the next render it is already
 * a few milliseconds stale and an equality check would snap the control back
 * to "Any time" while the filter was still applied.
 */
function periodOf(openedFrom: string | undefined): string {
  if (!openedFrom) return "any";
  const days = (Date.now() - new Date(openedFrom).getTime()) / 86_400_000;
  let closest = PERIODS[1];
  for (const option of PERIODS) {
    if (option.days === null) continue;
    if (Math.abs(option.days - days) < Math.abs((closest.days ?? 0) - days)) {
      closest = option;
    }
  }
  return closest.value;
}

function Filters({
  filters,
  onChange,
}: {
  filters: WorklistFilters;
  onChange: (patch: Partial<WorklistFilters>) => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-3 rounded-md border border-line bg-surface p-3">
      <label className="block">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
          Severity
        </span>
        <select
          value={filters.severity?.[0] ?? ""}
          onChange={(event) =>
            onChange({
              severity: event.target.value ? [event.target.value] : undefined,
            })
          }
          className="mt-1 block rounded-md border border-line px-3 py-1.5 text-sm"
        >
          <option value="">All</option>
          {SEVERITY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
          Opened
        </span>
        <select
          value={periodOf(filters.opened_from)}
          onChange={(event) =>
            onChange({ opened_from: sinceFor(event.target.value) })
          }
          className="mt-1 block rounded-md border border-line px-3 py-1.5 text-sm"
        >
          {PERIODS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block flex-1 min-w-[12rem]">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
          Patient name or MRN
        </span>
        <input
          value={filters.search ?? ""}
          onChange={(event) => onChange({ search: event.target.value || undefined })}
          className="mt-1 w-full rounded-md border border-line px-3 py-1.5 text-sm"
        />
      </label>

      <label className="flex items-center gap-2 text-sm text-ink-body">
        <input
          type="checkbox"
          checked={filters.mine_only ?? false}
          onChange={(event) => onChange({ mine_only: event.target.checked })}
          className="h-4 w-4"
        />
        Only mine
      </label>

      <label className="flex items-center gap-2 text-sm text-ink-body">
        <input
          type="checkbox"
          checked={filters.overdue_only ?? false}
          onChange={(event) => onChange({ overdue_only: event.target.checked })}
          className="h-4 w-4"
        />
        Overdue only
      </label>

      <label className="flex items-center gap-2 text-sm text-ink-body">
        <input
          type="checkbox"
          checked={filters.include_closed ?? false}
          onChange={(event) => onChange({ include_closed: event.target.checked })}
          className="h-4 w-4"
        />
        Include closed
      </label>
    </div>
  );
}
