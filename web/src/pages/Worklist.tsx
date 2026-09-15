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

/**
 * The severity rail down the left edge of a row.
 *
 * A `border-l` on a `<tr>` is **discarded** under `border-collapse: collapse` —
 * the row box has no border box of its own to paint. So the rail is an inset
 * box-shadow on the row's first *cell*, which is a real box and always paints.
 */
const ROW_RAIL: Record<string, string> = {
  critical: "shadow-[inset_4px_0_0_0_var(--rg-critical)]",
  follow_up: "shadow-[inset_4px_0_0_0_var(--rg-followup)]",
  normal: "shadow-[inset_4px_0_0_0_var(--rg-normal)]",
};

/**
 * The row wash.
 *
 * ⛔ Never `bg-critical-subtle/40`. The palette is bare `var(--rg-*)` strings,
 * which Tailwind cannot synthesise an alpha channel from, so every `/opacity`
 * modifier on a token is dropped **silently** — the critical row rendered with
 * no tint at all for three phases. `--rg-critical-row` is a named token that
 * exists precisely because a wash across a 1400px row has to be far weaker
 * than the same colour inside a 100px badge.
 */
const ROW_WASH: Record<string, string> = {
  // ★ The hover variant is set HERE, on the same utility and therefore at the
  // same specificity as the wash. `.rg-row:hover` is (0,2,0) and beats a bare
  // `.bg-surface-critical` at (0,1,0), so the generic hover used to *replace*
  // the severity tint with a neutral grey -- pointing at a critical row removed
  // the one colour saying it was critical. In light mode it also moved the row
  // the wrong way, darker than its resting state.
  //
  // Stating both here means hover LIFTS the tint and keeps the hue.
  critical: "bg-surface-critical hover:bg-surface-critical-hover",
  follow_up: "bg-surface-followup hover:bg-surface-followup-hover",
};

export function WorklistPage() {
  const { user } = useAuth();
  const [filters, setFilters] = useState<WorklistFilters>({ mine_only: true });
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isPending, isError, error, refetch, isFetching, dataUpdatedAt } =
    useWorklist(filters, cursor, { withTotal: true });

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

      {rows.length > 0 ? (
        <TriageBar rows={rows} updatedAt={dataUpdatedAt} isFetching={isFetching} />
      ) : null}

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
        /*
         * `tabIndex={0}` + `role="region"` + a name: WCAG 2.1.1. A horizontally
         * scrolling box that only a mouse can pan hides whole columns from a
         * keyboard user, and on this screen the hidden column is the one with
         * the escalation countdown in it.
         *
         * The `max-h` is what makes the sticky header mean anything: a scroll
         * container with no height constraint never scrolls vertically, so a
         * `sticky` thead inside it has nothing to stick against.
         */
        <div
          role="region"
          aria-label="Open flags"
          tabIndex={0}
          className={cn(
            "max-h-[70vh] overflow-auto rounded border border-line bg-surface",
            // A quiet edge while the 30s poll is in flight -- a token-weight
            // brand line, never `ring-brand/30`, which compiles to nothing.
            isFetching && "ring-1 ring-brand-line",
          )}
        >
          {/*
           * `min-w-[62rem]` is what makes the scroll container above mean
           * something. Without it the fixed columns simply squeezed forever and
           * the table never exceeded its container, so the horizontal scrollbar
           * — and the `tabIndex`/`role="region"` keyboard affordance built for
           * it — could never appear. Now the table stops shrinking at a usable
           * width and the region scrolls, which is the behaviour the WCAG 2.1.1
           * comment above was written for.
           */}
          <table className="w-full min-w-[62rem] table-fixed text-left text-data">
            <caption className="sr-only">
              Open flags, most critical first, then oldest
            </caption>
            {/*
             * `shadow-[inset_0_-1px_0_0_var(--rg-border)]` on every `<th>`, not
             * a `border-b` on the `<thead>`: under `border-collapse` the
             * collapsed border belongs to the table grid and stays behind while
             * the sticky header scrolls over the rows, leaving the header
             * floating with no underline at all.
             */}
            <thead className="sticky top-0 z-10 bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
              <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:font-medium [&>th]:shadow-[inset_0_-1px_0_0_var(--rg-border)]">
                <th scope="col" className="w-10">
                  <span className="sr-only">Select</span>
                </th>
                {/*
                 * ★ Explicit widths, because `min-w-*` does NOTHING here.
                 * Under `table-layout: fixed` a column is sized from the
                 * `width` property of the first row's cells and nothing else —
                 * `min-width` is not consulted. These two were `min-w-[14rem]`
                 * and `min-w-0`, i.e. `width: auto`, so they simply divided
                 * whatever the fixed columns left over: comfortable at 1600px,
                 * 197px at 1100px, and **37px at 780px**.
                 *
                 * Patient is fixed so the badges below line up into a stripe
                 * the eye can run down; Test is the one elastic column and
                 * absorbs the remainder.
                 */}
                <th scope="col" className="w-[16rem]">Patient</th>
                <th scope="col">Test</th>
                <th scope="col" className="w-[10rem]">Severity</th>
                <th scope="col" className="w-24 tabular">Age</th>
                <th scope="col" className="w-20">Rung</th>
                <th scope="col" className="w-40 tabular">Next escalation</th>
                <th scope="col" className="w-36 truncate">Owner</th>
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
  const rail = row.severity ? ROW_RAIL[row.severity] : undefined;
  const wash = row.severity ? ROW_WASH[row.severity] : undefined;

  return (
    <tr className={cn("rg-row", wash)}>
      {/* The rail lives on this cell, not on the `<tr>`. See ROW_RAIL. */}
      <td className={rail}>
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
          className="h-4 w-4 align-middle"
        />
      </td>

      {/*
       * One line, deliberately. Name and MRN stacked cost a second line on
       * every row in the table, and on a ward round the number of rows visible
       * at once is the whole point of this screen.
       */}
      {/*
       * `overflow-hidden` on the cell, `min-w-0` on the link.
       *
       * ★ Both are load-bearing and neither is obvious. A flex item's default
       * `min-width: auto` floors it at its **min-content** width, so `truncate`
       * alone never engages: the link renders at full intrinsic width and paints
       * straight over the next column. Measured at a 780px viewport the cell is
       * 37px wide and the link overhung it by 57px — which is what produced the
       * overprinted `DEVERITY` header and `NSEEDt-1094sified` row.
       *
       * `e2e/layout.spec.ts` measures this. jsdom cannot: it performs no layout
       * at all, so every unit test passed while the page was visibly broken.
       */}
      <td className="overflow-hidden">
        <div className="flex items-baseline gap-2">
          <Link
            to={`/cases/${row.case_id}`}
            className="min-w-0 truncate font-medium text-brand-text underline-offset-2 hover:underline"
          >
            {row.patient_name}
          </Link>
          <span className="shrink-0 text-xs text-ink-muted">{row.mrn}</span>
          {row.reopened_count > 0 ? (
            <span
              className="shrink-0 rounded bg-followup-subtle px-1.5 text-xs font-medium text-followup-text"
              title={`This case has been reopened ${row.reopened_count} times.`}
            >
              Reopened {row.reopened_count}×
            </span>
          ) : null}
        </div>
      </td>

      {/*
       * The only elastic column. The summary is a secondary line clipped to
       * one row-height: it used to wrap to three lines and was the single
       * biggest cause of rows varying between 36px and 70px. The full text is
       * on `title`, and the whole of it is on the case page.
       */}
      <td className="min-w-0">
        <div className="truncate font-medium text-ink">{row.test_name}</div>
        <div className="truncate text-xs text-ink-body" title={row.summary}>
          {row.summary}
        </div>
      </td>

      <td>
        {row.severity ? (
          <SeverityBadge severity={row.severity} />
        ) : (
          <span className="text-xs text-ink-muted">Not yet classified</span>
        )}
      </td>
      <td className="tabular text-ink-body">{formatAge(row.age_seconds)}</td>
      <td className="text-ink-body">
        {row.escalation_level === null ? "—" : `Rung ${row.escalation_level}`}
      </td>
      <td className="tabular">
        {countdown ? (
          countdown.overdue ? (
            /*
             * A filled pill, not red text. "Overdue" means a rung that should
             * already have fired has not, and red type in a column of grey type
             * is the easiest thing on this screen to skim past.
             */
            <span className="inline-flex items-center gap-1 rounded bg-critical-subtle px-2 py-0.5 font-semibold text-critical-text ring-1 ring-critical-line">
              <ClockIcon />
              {countdown.label}
            </span>
          ) : (
            <span className="text-ink-body">{countdown.label}</span>
          )
        ) : (
          <span className="text-ink-muted">None scheduled</span>
        )}
      </td>
      <td className="text-ink-body">
        <span className="block truncate" title={row.owner_name ?? "Unassigned"}>
          {row.owner_name ?? "Unassigned"}
        </span>
      </td>
    </tr>
  );
}

function ClockIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      className="h-3.5 w-3.5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5V8l2.4 1.4" strokeLinecap="round" />
    </svg>
  );
}

/**
 * The triage summary.
 *
 * Without it the only way to answer "how many criticals am I looking at?" is to
 * count rows, which is exactly the arithmetic this product exists to stop
 * people doing by eye at 3am.
 *
 * **The counts are labelled "on this page" and that label is not decoration.**
 * They are derived from the rows actually loaded, and the list is paginated —
 * stating them as a ward total would be inventing a number the server never
 * sent. `data.total_open` is the only server-side figure available and it is
 * shown separately, in the header, under its own name.
 *
 * `aria-live="polite"`, never `assertive`: this updates every 30 seconds, and
 * an assertive region interrupts a screen-reader user mid-sentence twice a
 * minute until they turn the screen off.
 */
function TriageBar({
  rows,
  updatedAt,
  isFetching,
}: {
  rows: WorklistRow[];
  updatedAt: number;
  isFetching: boolean;
}) {
  const counts = useMemo(
    () => ({
      critical: rows.filter((row) => row.severity === "critical").length,
      followUp: rows.filter((row) => row.severity === "follow_up").length,
      overdue: rows.filter(
        (row) =>
          row.seconds_to_next_escalation !== null &&
          row.seconds_to_next_escalation < 0,
      ).length,
    }),
    [rows],
  );

  // en-GB, 24-hour: "14:07:22". A ward runs on a 24-hour clock, and an
  // am/pm suffix on a freshness stamp is two extra characters to parse.
  const stamp = updatedAt
    ? new Date(updatedAt).toLocaleTimeString("en-GB", { hour12: false })
    : null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-wrap items-center gap-2 rounded border border-line bg-surface px-3 py-2 text-data"
    >
      <span className="font-medium text-ink">On this page</span>
      <TriageCount
        n={counts.critical}
        label="critical"
        tone="border-critical-line bg-critical-subtle text-critical-text"
      />
      <TriageCount
        n={counts.followUp}
        label="needs follow-up"
        tone="border-followup-line bg-followup-subtle text-followup-text"
      />
      <TriageCount
        n={counts.overdue}
        label="escalation overdue"
        tone="border-critical-line bg-critical-subtle text-critical-text"
      />
      <span className="ml-auto text-xs text-ink-muted">
        {isFetching || !stamp ? "Updating…" : `Updated ${stamp}`}
      </span>
    </div>
  );
}

function TriageCount({ n, label, tone }: { n: number; label: string; tone: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-0.5",
        // Zero of something is worth saying, but not worth colouring: a row of
        // saturated chips reading 0 trains the eye to ignore the chips.
        n > 0 ? tone : "border-line bg-surface-sunken text-ink-muted",
      )}
    >
      <span className="tabular font-semibold">{n}</span>
      <span>{label}</span>
    </span>
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
    <div className="rounded border border-line bg-surface p-4">
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
            className="mt-1 block rounded border border-line px-3 py-2 text-sm"
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
            className="mt-1 w-full rounded border border-line px-3 py-2 text-sm"
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
    <div className="flex flex-wrap items-end gap-3 rounded border border-line bg-surface p-3">
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
          className="mt-1 block rounded border border-line px-3 py-1.5 text-sm"
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
          className="mt-1 block rounded border border-line px-3 py-1.5 text-sm"
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
          className="mt-1 w-full rounded border border-line px-3 py-1.5 text-sm"
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
