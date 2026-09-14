/**
 * The auditor's view. Phase 5.5.
 *
 *     `GET /api/audit/verify?from=&to=` → recomputes the chain, returns the
 *     first break if any
 *     Audit viewer UI for the `auditor` role, exportable to CSV
 *
 * **Verification is a button, not a poll.** Recomputing every hash is real
 * work, and a number that refreshes on its own invites nobody to look at it.
 * An auditor presses the button and reads the answer.
 *
 * The result of a broken chain is deliberately loud and specific: it names
 * the sequence number where the break is and what kind of break it is,
 * because "the audit log has been tampered with" and "row 4,812 no longer
 * matches its hash" lead to very different next steps.
 */

import { useState } from "react";

import { api } from "../api/client";
import { useAnchors, useAuditTrail, useVerifyChain } from "../api/queries5";
import type { AuditRow } from "../api/types5";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { Empty, ErrorState, Loading } from "../components/ui/States";

export function AuditTrailPage() {
  const [filters, setFilters] = useState<Record<string, unknown>>({});
  const [afterSeq, setAfterSeq] = useState<number | null>(null);
  const [verifying, setVerifying] = useState(false);

  const { data, isPending, isError, error, refetch } = useAuditTrail(filters, afterSeq);
  const verification = useVerifyChain(null, null, verifying);

  function update(patch: Record<string, unknown>) {
    setAfterSeq(null);
    setFilters((current) => ({ ...current, ...patch }));
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold text-ink">Audit trail</h1>
        <p className="text-sm text-ink-body">
          Every row is hash-chained to the one before it.
        </p>
        <div className="ml-auto flex gap-2">
          <Button
            variant="secondary"
            onClick={() => {
              setVerifying(true);
              void verification.refetch();
            }}
            disabled={verification.isFetching}
          >
            {verification.isFetching ? "Verifying…" : "Verify the chain"}
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              void api.download(
                `/audit/export.csv${toSearch(filters)}`,
                `audit-${new Date().toISOString().slice(0, 10)}.csv`,
              )
            }
          >
            Export CSV
          </Button>
        </div>
      </header>

      {verifying && verification.data ? (
        <VerificationResult result={verification.data} />
      ) : null}
      {verifying && verification.isError ? (
        <ErrorState error={verification.error} fallbackTitle="Could not verify the chain" />
      ) : null}

      <Filters onChange={update} />

      {isPending ? <Loading label="Loading the audit trail…" /> : null}
      {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : null}
      {!isPending && !isError && (data?.rows.length ?? 0) === 0 ? (
        <Empty title="No audit entries match these filters" />
      ) : null}

      {data && data.rows.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
              <tr>
                <th scope="col" className="px-3 py-2">Seq</th>
                <th scope="col" className="px-3 py-2">When</th>
                <th scope="col" className="px-3 py-2">Who</th>
                <th scope="col" className="px-3 py-2">Action</th>
                <th scope="col" className="px-3 py-2">Entity</th>
                <th scope="col" className="px-3 py-2">Change</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <Row key={row.seq} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="flex gap-2">
        {afterSeq !== null ? (
          <Button variant="secondary" onClick={() => setAfterSeq(null)}>
            Back to the start
          </Button>
        ) : null}
        {data?.next_after_seq ? (
          <Button variant="secondary" onClick={() => setAfterSeq(data.next_after_seq)}>
            Next 100
          </Button>
        ) : null}
      </div>

      <Anchors />
    </div>
  );
}

function toSearch(filters: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

function VerificationResult({
  result,
}: {
  result: {
    intact: boolean;
    rows_checked: number;
    first_break_seq: number | null;
    first_break_reason: string | null;
    head_seq: number | null;
    head_hash: string | null;
  };
}) {
  if (!result.intact) {
    return (
      <Banner tone="danger" title="The audit chain is broken">
        <p>
          The first break is at sequence <strong>{result.first_break_seq}</strong>.
        </p>
        <p className="mt-1">{result.first_break_reason}</p>
        <p className="mt-2">
          {result.rows_checked} rows verified before the break. Escalate this —
          it means a row was changed, removed or inserted after it was written.
        </p>
      </Banner>
    );
  }

  return (
    <Banner tone="success" title="The audit chain is intact">
      <p>
        {result.rows_checked} rows recomputed and matched. Head is sequence{" "}
        {result.head_seq}.
      </p>
      {result.head_hash ? (
        <p className="mt-1 break-all font-mono text-xs">{result.head_hash}</p>
      ) : null}
    </Banner>
  );
}

function Row({ row }: { row: AuditRow }) {
  const [open, setOpen] = useState(false);
  const hasDiff = row.before !== null || row.after !== null;

  return (
    <>
      <tr className="border-b border-slate-100">
        <td className="px-3 py-2 font-mono text-xs text-ink-muted">{row.seq}</td>
        <td className="px-3 py-2 text-xs text-ink-body">
          {new Date(row.occurred_at).toLocaleString()}
        </td>
        <td className="px-3 py-2">
          {row.actor_name ?? <span className="text-ink-muted">system</span>}
          {row.actor_employee_code ? (
            <div className="text-xs text-ink-muted">{row.actor_employee_code}</div>
          ) : null}
          {row.actor_ip ? (
            <div className="font-mono text-xs text-ink-muted">{row.actor_ip}</div>
          ) : null}
        </td>
        <td className="px-3 py-2">
          <span className="font-mono text-xs">{row.action}</span>
          {row.break_glass_reason ? (
            <div className="mt-1 rounded bg-followup-subtle px-2 py-1 text-xs text-followup-text">
              Break-glass: {row.break_glass_reason}
            </div>
          ) : null}
        </td>
        <td className="px-3 py-2 text-xs text-ink-body">
          {row.entity_type}
          <div className="font-mono text-ink-muted">{row.entity_id.slice(0, 8)}…</div>
        </td>
        <td className="px-3 py-2">
          {hasDiff ? (
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              className="text-xs text-brand-text underline"
            >
              {open ? "Hide" : "Show"}
            </button>
          ) : (
            <span className="text-xs text-ink-muted">—</span>
          )}
        </td>
      </tr>
      {open ? (
        <tr className="border-b border-slate-100 bg-surface-sunken">
          <td colSpan={6} className="px-3 py-3">
            <div className="grid gap-3 md:grid-cols-2">
              <Json label="Before" value={row.before} />
              <Json label="After" value={row.after} />
            </div>
            <p className="mt-3 break-all font-mono text-xs text-ink-muted">
              row_hash {row.row_hash}
            </p>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Json({ label, value }: { label: string; value: Record<string, unknown> | null }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-body">
        {label}
      </h3>
      <pre className="mt-1 overflow-x-auto rounded bg-surface p-2 text-xs text-ink">
        {value === null ? "—" : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function Filters({ onChange }: { onChange: (patch: Record<string, unknown>) => void }) {
  return (
    <div className="flex flex-wrap items-end gap-3 rounded-md border border-line bg-surface p-3">
      <label className="block">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
          Action
        </span>
        <input
          placeholder="case.closed"
          onChange={(event) => onChange({ action: event.target.value || undefined })}
          className="mt-1 block rounded-md border border-line px-3 py-1.5 text-sm"
        />
      </label>

      <label className="block">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
          Entity ID
        </span>
        <input
          onChange={(event) => onChange({ entity_id: event.target.value || undefined })}
          className="mt-1 block rounded-md border border-line px-3 py-1.5 font-mono text-sm"
        />
      </label>

      <label className="flex items-center gap-2 text-sm text-ink-body">
        <input
          type="checkbox"
          onChange={(event) => onChange({ break_glass_only: event.target.checked })}
          className="h-4 w-4"
        />
        Break-glass only
      </label>
    </div>
  );
}

function Anchors() {
  const { data } = useAnchors();
  if (!data || data.length === 0) return null;

  return (
    <section className="rounded-md border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">Nightly notarisations</h2>
      <p className="mt-1 text-sm text-ink-body">
        The head hash published each night. Export these off this machine —
        an anchor that only exists here is no harder to rewrite than the log.
      </p>
      <ul className="mt-3 space-y-1 text-xs">
        {data.slice(0, 10).map((anchor) => (
          <li key={anchor.anchored_at} className="flex flex-wrap gap-2">
            <time className="text-ink-muted">
              {new Date(anchor.anchored_at).toLocaleString()}
            </time>
            <span className={anchor.chain_intact ? "text-normal-text" : "font-semibold text-critical-text"}>
              {anchor.chain_intact ? "intact" : `BROKEN at ${anchor.first_break_seq}`}
            </span>
            <span className="text-ink-muted">seq {anchor.head_seq}</span>
            <span className="break-all font-mono text-ink-muted">
              {anchor.head_hash.slice(0, 24)}…
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
