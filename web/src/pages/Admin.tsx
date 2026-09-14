/**
 * The administrator's screens. Phase 5.4.
 *
 *     Admin: user management, roster, panic thresholds editor, escalation
 *     chain editor, keyword editor, notification provider health, **NODE B
 *     status and kill switch (`LLM_ENABLED`)**
 *
 * The kill switch leads, because it is the control someone reaches for in an
 * incident and a control you have to hunt for is not a control. Everything
 * else is a table.
 *
 * The read-only editors (thresholds, chain) show their data and say plainly
 * where the write path is. A half-built form that silently drops a field is
 * worse for a clinical threshold than an honest "use the API for this".
 */

import { useState } from "react";

import {
  useAdminUsers,
  useEscalationChain,
  useKeywords,
  useKillSwitch,
  useNodeBStatus,
  useOverrides,
  usePanicThresholds,
  useProviderHealth,
  useResetPassword,
  useUpdateKeyword,
  useUpdateUser,
} from "../api/queries5";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { Empty, ErrorState, Loading } from "../components/ui/States";
import { cn } from "../lib/cn";

const TABS = [
  { id: "node-b", label: "NODE B" },
  { id: "users", label: "Users" },
  { id: "keywords", label: "Keywords" },
  { id: "thresholds", label: "Panic thresholds" },
  { id: "chain", label: "Escalation chain" },
  { id: "providers", label: "Notification providers" },
  { id: "overrides", label: "Overrides" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function AdminPage() {
  const [tab, setTab] = useState<TabId>("node-b");

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-ink">Administration</h1>

      <div role="tablist" className="flex flex-wrap gap-1 border-b border-line">
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            type="button"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
              tab === item.id
                ? "border-blue-700 text-brand-text"
                : "border-transparent text-ink-body hover:text-ink",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "node-b" ? <NodeBPanel /> : null}
      {tab === "users" ? <UsersPanel /> : null}
      {tab === "keywords" ? <KeywordsPanel /> : null}
      {tab === "thresholds" ? <ThresholdsPanel /> : null}
      {tab === "chain" ? <ChainPanel /> : null}
      {tab === "providers" ? <ProvidersPanel /> : null}
      {tab === "overrides" ? <OverridesPanel /> : null}
    </div>
  );
}

// ── NODE B and the kill switch ─────────────────────────────────────────

function NodeBPanel() {
  const { data, isPending, isError, error, refetch } = useNodeBStatus();
  const killSwitch = useKillSwitch();
  const [reason, setReason] = useState("");

  if (isPending) return <Loading label="Checking NODE B…" />;
  if (isError || !data) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  const turningOff = data.llm_enabled;

  return (
    <section className="space-y-4">
      <Banner tone="info" title="Turning this off costs nothing that keeps a patient safe">
        <p>{data.safety_note}</p>
      </Banner>

      <dl className="grid gap-3 rounded-md border border-line bg-surface p-4 sm:grid-cols-2">
        <Field label="Inference enabled">
          {data.llm_enabled ? "Yes" : "No"}
          <span className="ml-2 text-xs text-ink-muted">
            (set in the {data.llm_enabled_source})
          </span>
        </Field>
        <Field label="Reachable right now">
          {data.reachable ? (
            <span className="text-normal-text">Yes</span>
          ) : (
            <span className="text-ink-body">
              No{data.probe_error ? ` — ${data.probe_error}` : ""}
            </span>
          )}
        </Field>
        <Field label="Address">
          <span className="font-mono text-xs">{data.base_url}</span>
        </Field>
        <Field label="Model">
          <span className="font-mono text-xs">{data.model}</span>
        </Field>
        {data.kill_switch_reason ? (
          <Field label="Turned off because">{data.kill_switch_reason}</Field>
        ) : null}
      </dl>

      <form
        className="space-y-3 rounded-md border border-line bg-surface p-4"
        onSubmit={(event) => {
          event.preventDefault();
          killSwitch.mutate(
            { llm_enabled: !turningOff, reason },
            { onSuccess: () => setReason("") },
          );
        }}
      >
        <h2 className="text-sm font-semibold text-ink">
          {turningOff ? "Turn inference off" : "Turn inference back on"}
        </h2>
        <label className="block">
          <span className="text-sm font-medium text-ink">
            Why? This is shown to users and written to the audit log.
          </span>
          <input
            required
            minLength={5}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
          />
        </label>
        {killSwitch.isError ? <ErrorState error={killSwitch.error} /> : null}
        <Button
          type="submit"
          variant={turningOff ? "danger" : "primary"}
          disabled={killSwitch.isPending || reason.trim().length < 5}
        >
          {killSwitch.isPending
            ? "Saving…"
            : turningOff
              ? "Turn inference off"
              : "Turn inference on"}
        </Button>
        <p className="text-xs text-ink-muted">
          Takes effect within 10 seconds. No restart, no shell.
        </p>
      </form>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-ink-body">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

// ── users ──────────────────────────────────────────────────────────────

function UsersPanel() {
  const [includeInactive, setIncludeInactive] = useState(false);
  const { data, isPending, isError, error, refetch } = useAdminUsers(includeInactive);
  const update = useUpdateUser();
  const reset = useResetPassword();
  const [issued, setIssued] = useState<{ code: string; password: string } | null>(null);

  if (isPending) return <Loading />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;

  return (
    <section className="space-y-3">
      <label className="flex items-center gap-2 text-sm text-ink-body">
        <input
          type="checkbox"
          checked={includeInactive}
          onChange={(event) => setIncludeInactive(event.target.checked)}
          className="h-4 w-4"
        />
        Include deactivated accounts
      </label>

      {issued ? (
        <Banner tone="warning" title="Temporary password issued">
          <p>
            <strong>{issued.code}</strong> — password{" "}
            <code className="font-mono">{issued.password}</code>
          </p>
          <p className="mt-1">
            Shown once. It is not recoverable; issue another reset if it is lost.
          </p>
          <Button variant="secondary" className="mt-2" onClick={() => setIssued(null)}>
            Dismiss
          </Button>
        </Banner>
      ) : null}

      <div className="overflow-x-auto rounded-md border border-line bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
            <tr>
              <th scope="col" className="px-3 py-2">Name</th>
              <th scope="col" className="px-3 py-2">Code</th>
              <th scope="col" className="px-3 py-2">Role</th>
              <th scope="col" className="px-3 py-2">Department</th>
              <th scope="col" className="px-3 py-2">Status</th>
              <th scope="col" className="px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((user) => (
              <tr key={user.id} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-2 text-ink">{user.full_name}</td>
                <td className="px-3 py-2 font-mono text-xs text-ink-body">
                  {user.employee_code}
                </td>
                <td className="px-3 py-2 text-ink-body">{user.role.replace("_", " ")}</td>
                <td className="px-3 py-2 text-ink-body">
                  {user.department_name ?? "—"}
                </td>
                <td className="px-3 py-2 text-xs">
                  {!user.is_active ? (
                    <span className="text-ink-muted">Deactivated</span>
                  ) : user.locked_until ? (
                    <span className="font-medium text-critical-text">
                      Locked ({user.failed_login_count} failures)
                    </span>
                  ) : user.must_change_password ? (
                    <span className="text-followup-text">Must change password</span>
                  ) : (
                    <span className="text-normal-text">Active</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {user.locked_until ? (
                      <Button
                        variant="secondary"
                        className="px-2 py-1 text-xs"
                        onClick={() => update.mutate({ id: user.id, unlock: true })}
                      >
                        Unlock
                      </Button>
                    ) : null}
                    <Button
                      variant="secondary"
                      className="px-2 py-1 text-xs"
                      onClick={() =>
                        update.mutate({ id: user.id, is_active: !user.is_active })
                      }
                    >
                      {user.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                    <Button
                      variant="secondary"
                      className="px-2 py-1 text-xs"
                      onClick={() =>
                        reset.mutate(user.id, {
                          onSuccess: (result) =>
                            setIssued({
                              code: user.employee_code,
                              password: result.temporary_password,
                            }),
                        })
                      }
                    >
                      Reset password
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {update.isError ? <ErrorState error={update.error} /> : null}
    </section>
  );
}

// ── keywords ───────────────────────────────────────────────────────────

function KeywordsPanel() {
  const { data, isPending, isError, error, refetch } = useKeywords(true);
  const update = useUpdateKeyword();

  if (isPending) return <Loading />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (!data || data.length === 0) return <Empty title="No clinical keywords configured" />;

  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
          <tr>
            <th scope="col" className="px-3 py-2">Term</th>
            <th scope="col" className="px-3 py-2">Category</th>
            <th scope="col" className="px-3 py-2">Severity</th>
            <th scope="col" className="px-3 py-2">Negation checked</th>
            <th scope="col" className="px-3 py-2">Active</th>
          </tr>
        </thead>
        <tbody>
          {data.map((keyword) => (
            <tr key={keyword.id} className="border-b border-slate-100 last:border-0">
              <td className="px-3 py-2 text-ink">{keyword.term}</td>
              <td className="px-3 py-2 text-ink-body">{keyword.category}</td>
              <td className="px-3 py-2 text-ink-body">{keyword.severity}</td>
              <td className="px-3 py-2 text-ink-body">
                {keyword.requires_negation_check ? "Yes" : "No"}
              </td>
              <td className="px-3 py-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={keyword.active}
                    onChange={(event) =>
                      update.mutate({ id: keyword.id, active: event.target.checked })
                    }
                    className="h-4 w-4"
                  />
                  <span className="sr-only">Active</span>
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── panic thresholds ───────────────────────────────────────────────────

function ThresholdsPanel() {
  const { data, isPending, isError, error, refetch } = usePanicThresholds();

  if (isPending) return <Loading />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;

  return (
    <section className="space-y-3">
      <Banner tone="warning" title="These are clinical thresholds">
        <p>
          Adding a threshold supersedes the live one rather than editing it, so
          a decision made months ago can still be explained with the number
          that was in force then. Every threshold needs a documented source —
          your hospital&rsquo;s own critical value list, not the internet.
        </p>
      </Banner>

      {!data || data.length === 0 ? (
        <Empty title="No panic thresholds configured">
          Rule A falls back to reference-range comparison until thresholds are
          seeded from the hospital&rsquo;s critical value list.
        </Empty>
      ) : (
        <div className="overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
              <tr>
                <th scope="col" className="px-3 py-2">Test</th>
                <th scope="col" className="px-3 py-2">Sex</th>
                <th scope="col" className="px-3 py-2">Critical low</th>
                <th scope="col" className="px-3 py-2">Critical high</th>
                <th scope="col" className="px-3 py-2">Unit</th>
                <th scope="col" className="px-3 py-2">Source</th>
                <th scope="col" className="px-3 py-2">In force from</th>
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={row.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-3 py-2 font-mono text-xs text-ink">
                    {row.test_code}
                  </td>
                  <td className="px-3 py-2 text-ink-body">{row.sex}</td>
                  <td className="px-3 py-2 text-ink-body">{row.critical_low ?? "—"}</td>
                  <td className="px-3 py-2 text-ink-body">{row.critical_high ?? "—"}</td>
                  <td className="px-3 py-2 text-ink-body">{row.unit ?? "—"}</td>
                  <td className="px-3 py-2 text-ink-body">{row.source}</td>
                  <td className="px-3 py-2 text-xs text-ink-body">
                    {new Date(row.effective_from).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ── escalation chain ───────────────────────────────────────────────────

function ChainPanel() {
  const { data, isPending, isError, error, refetch } = useEscalationChain(null);

  if (isPending) return <Loading />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (!data || data.length === 0) return <Empty title="No escalation rungs configured" />;

  return (
    <section className="space-y-3">
      <p className="text-sm text-ink-body">
        Rows with no department are the global default that every department
        inherits until it defines its own. Editing a rung applies to cases
        flagged afterwards — timers already scheduled keep their original time.
      </p>

      <div className="overflow-x-auto rounded-md border border-line bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
            <tr>
              <th scope="col" className="px-3 py-2">Department</th>
              <th scope="col" className="px-3 py-2">Severity</th>
              <th scope="col" className="px-3 py-2">Rung</th>
              <th scope="col" className="px-3 py-2">Goes to</th>
              <th scope="col" className="px-3 py-2">After</th>
              <th scope="col" className="px-3 py-2">Channels</th>
            </tr>
          </thead>
          <tbody>
            {data.map((rung) => (
              <tr key={rung.id} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-2 text-ink-body">
                  {rung.department_name ?? (
                    <span className="italic text-ink-muted">Global default</span>
                  )}
                </td>
                <td className="px-3 py-2 text-ink-body">{rung.severity}</td>
                <td className="px-3 py-2 text-ink-body">{rung.level}</td>
                <td className="px-3 py-2 text-ink-body">
                  {rung.target_type.replace("_", " ")}
                </td>
                <td className="px-3 py-2 text-ink-body">{rung.delay_minutes} min</td>
                <td className="px-3 py-2 text-xs text-ink-body">
                  {rung.channels.join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ── notification providers ─────────────────────────────────────────────

function ProvidersPanel() {
  const { data, isPending, isError, error, refetch } = useProviderHealth();

  if (isPending) return <Loading />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (!data || data.length === 0) {
    return <Empty title="No notifications sent in the last 24 hours" />;
  }

  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
          <tr>
            <th scope="col" className="px-3 py-2">Channel</th>
            <th scope="col" className="px-3 py-2">Adapter</th>
            <th scope="col" className="px-3 py-2">Sent</th>
            <th scope="col" className="px-3 py-2">Failed</th>
            <th scope="col" className="px-3 py-2">Suppressed</th>
            <th scope="col" className="px-3 py-2">Last error</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.channel} className="border-b border-slate-100 last:border-0">
              <td className="px-3 py-2 text-ink">{row.channel}</td>
              <td className="px-3 py-2 text-xs text-ink-body">{row.adapter}</td>
              <td className="px-3 py-2 text-ink-body">{row.sent_24h}</td>
              <td
                className={cn(
                  "px-3 py-2",
                  row.failed_24h > 0 ? "font-semibold text-critical-text" : "text-ink-body",
                )}
              >
                {row.failed_24h}
                {row.failed_24h > 0 ? ` (${(row.failure_rate * 100).toFixed(0)}%)` : null}
              </td>
              <td className="px-3 py-2 text-ink-body">{row.suppressed_24h}</td>
              <td className="px-3 py-2 text-xs text-ink-body">
                {row.last_error ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── overrides ──────────────────────────────────────────────────────────

function OverridesPanel() {
  const { data, isPending, isError, error, refetch } = useOverrides();

  if (isPending) return <Loading />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (!data || data.length === 0) {
    return <Empty title="No discharge overrides recorded" />;
  }

  return (
    <section className="space-y-3">
      <p className="text-sm text-ink-body">
        Every discharge that bypassed the contract gate. A rising count means
        the gate is being routed around — a process finding, not a bug.
      </p>

      <div className="overflow-x-auto rounded-md border border-line bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-surface-sunken text-xs uppercase tracking-wide text-ink-body">
            <tr>
              <th scope="col" className="px-3 py-2">When</th>
              <th scope="col" className="px-3 py-2">Patient</th>
              <th scope="col" className="px-3 py-2">Reason</th>
              <th scope="col" className="px-3 py-2">Overridden by</th>
              <th scope="col" className="px-3 py-2">Approved by</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.id} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-2 text-xs text-ink-body">
                  {new Date(row.created_at).toLocaleString()}
                </td>
                <td className="px-3 py-2 text-ink">
                  {row.patient_name ?? "—"}
                  <div className="text-xs text-ink-muted">{row.mrn ?? ""}</div>
                </td>
                <td className="px-3 py-2 text-ink-body">
                  <div className="font-mono text-xs">{row.reason_code}</div>
                  <div className="max-w-md text-sm">{row.reason_text}</div>
                </td>
                <td className="px-3 py-2 text-ink-body">
                  {row.overridden_by_name ?? "—"}
                </td>
                <td className="px-3 py-2 text-ink-body">
                  {row.approved_by_name ?? (
                    <span className="text-followup-text">Not approved</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
