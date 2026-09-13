/**
 * Reports and metrics. Phase 5.6.
 *
 * The closure-reason chart is the one to read first, and the page says so.
 * *"A spike in `not_clinically_relevant` means a threshold problem"* — the
 * metric exists because the reason is both legitimate and abusable, and a
 * dashboard that buries it among nine others has hidden the only number that
 * tells you the rule engine is flagging too much.
 *
 * No charting library. Bars are divs with a width. A hospital dashboard that
 * needs 200 KB of JavaScript to draw seven rectangles has made a trade nobody
 * asked for, and these numbers are read as much on paper as on screen.
 */

import { useState } from "react";

import { api } from "../api/client";
import { useMetrics, usePerDoctor } from "../api/queries5";
import type { MetricsSummary } from "../api/types5";
import { useAuth } from "../auth/AuthProvider";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { Empty, ErrorState, Loading } from "../components/ui/States";
import { formatDuration } from "../lib/countdown";
import { cn } from "../lib/cn";

const WINDOWS = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const STAGE_LABELS: Record<string, string> = {
  discharge_to_result: "Discharge → result",
  result_to_flag: "Result → flag",
  flag_to_acknowledgement: "Flag → acknowledgement",
  discharge_to_acknowledgement: "Discharge → acknowledgement (total)",
};

export function ReportsPage() {
  const { user } = useAuth();
  const [days, setDays] = useState(30);
  const { data, isPending, isError, error, refetch } = useMetrics(days, null);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900">Reports</h1>
        <div className="flex gap-1">
          {WINDOWS.map((window) => (
            <button
              key={window.days}
              type="button"
              onClick={() => setDays(window.days)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium",
                days === window.days
                  ? "bg-blue-50 text-blue-800"
                  : "text-slate-600 hover:bg-slate-100",
              )}
            >
              {window.label}
            </button>
          ))}
        </div>

        <div className="ml-auto flex gap-2">
          <Button
            variant="secondary"
            onClick={() =>
              void api.download(
                `/reports/summary.csv?days=${days}`,
                `metrics-${new Date().toISOString().slice(0, 10)}.csv`,
              )
            }
          >
            Export CSV
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              void api.download("/reports/nabh-monthly.pdf", "nabh-monthly.pdf")
            }
          >
            NABH monthly PDF
          </Button>
        </div>
      </header>

      {isPending ? <Loading label="Computing…" /> : null}
      {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : null}

      {data ? (
        <>
          <AlertFatigue report={data} />
          <ClosureReasons report={data} />
          <Turnaround report={data} />
          <AgeBuckets report={data} />
          <Escalations report={data} />
          <PatientContact report={data} />
          <OpenLabFlags report={data} />
        </>
      ) : null}

      {user && ["unit_head", "admin"].includes(user.role) ? (
        <PerDoctor days={days} />
      ) : null}
    </div>
  );
}

function Card({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      {hint ? <p className="mt-0.5 text-xs text-slate-600">{hint}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Bar({ value, max, tone = "blue" }: { value: number; max: number; tone?: string }) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-2 w-full rounded bg-slate-100">
      <div
        className={cn("h-2 rounded", tone === "red" ? "bg-red-600" : "bg-blue-600")}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

function AlertFatigue({ report }: { report: MetricsSummary }) {
  const rate = report.flag_rate;
  return (
    <Card
      title="Flag rate per 100 discharges"
      hint="The alert fatigue metric. A rising number means clinicians are being asked to look at more, not that more is wrong."
    >
      <div className="flex flex-wrap gap-8">
        <Metric label="Discharges" value={String(rate.discharges)} />
        <Metric label="Flags per 100" value={String(rate.flags_per_100_discharges)} />
        <Metric
          label="Critical per 100"
          value={String(rate.critical_per_100_discharges)}
        />
      </div>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-2xl font-semibold text-slate-900">{value}</div>
      <div className="text-xs uppercase tracking-wide text-slate-600">{label}</div>
    </div>
  );
}

function ClosureReasons({ report }: { report: MetricsSummary }) {
  const rows = report.closure_reasons;
  const max = Math.max(1, ...rows.map((row) => row.count));
  const irrelevant = rows.find((row) => row.closure_reason === "not_clinically_relevant");

  return (
    <Card
      title="Closure reasons"
      hint="Clinician closures only — auto-closed normals are excluded, because counting them as judgements would flatter every number here."
    >
      {irrelevant && irrelevant.share > 0.3 ? (
        <Banner
          tone="warning"
          title="A high share of cases are closed as 'not clinically relevant'"
          className="mb-3"
        >
          <p>
            {(irrelevant.share * 100).toFixed(0)}% of closures. That usually
            means a threshold is flagging too much, not that the ward is tidy.
            Review the panic thresholds and keywords.
          </p>
        </Banner>
      ) : null}

      {rows.length === 0 ? (
        <Empty title="No cases have been closed in this window" />
      ) : (
        <ul className="space-y-2">
          {rows.map((row) => (
            <li key={row.closure_reason}>
              <div className="flex justify-between text-sm">
                <span className="text-slate-800">
                  {row.closure_reason.replace(/_/g, " ")}
                </span>
                <span className="text-slate-600">
                  {row.count} ({(row.share * 100).toFixed(0)}%)
                </span>
              </div>
              <Bar
                value={row.count}
                max={max}
                tone={row.closure_reason === "not_clinically_relevant" ? "red" : "blue"}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Turnaround({ report }: { report: MetricsSummary }) {
  return (
    <Card
      title="Turnaround"
      hint="Median and 90th percentile, not an average — these distributions have long tails and a mean describes no actual patient."
    >
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-600">
          <tr>
            <th scope="col" className="py-1">Stage</th>
            <th scope="col" className="py-1">p50</th>
            <th scope="col" className="py-1">p90</th>
            <th scope="col" className="py-1">n</th>
          </tr>
        </thead>
        <tbody>
          {report.turnaround.map((stat) => (
            <tr key={stat.stage} className="border-t border-slate-100">
              <td className="py-2 text-slate-800">
                {STAGE_LABELS[stat.stage] ?? stat.stage}
              </td>
              <td className="py-2 text-slate-700">
                {stat.p50_seconds === null ? "—" : formatDuration(stat.p50_seconds)}
              </td>
              <td className="py-2 text-slate-700">
                {stat.p90_seconds === null ? "—" : formatDuration(stat.p90_seconds)}
              </td>
              <td className="py-2 text-slate-500">{stat.sample_size}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function AgeBuckets({ report }: { report: MetricsSummary }) {
  const max = Math.max(1, ...report.age_buckets.map((bucket) => bucket.count));
  return (
    <Card
      title="Open cases by age"
      hint="Not limited to the reporting window: an open case from two months ago is the most important row on this page."
    >
      <ul className="space-y-2">
        {report.age_buckets.map((bucket) => (
          <li key={bucket.label}>
            <div className="flex justify-between text-sm">
              <span className="text-slate-800">{bucket.label}</span>
              <span className="text-slate-600">
                {bucket.count}
                {bucket.critical_count > 0 ? (
                  <span className="ml-2 font-medium text-red-800">
                    {bucket.critical_count} critical
                  </span>
                ) : null}
              </span>
            </div>
            <Bar
              value={bucket.count}
              max={max}
              tone={bucket.critical_count > 0 ? "red" : "blue"}
            />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Escalations({ report }: { report: MetricsSummary }) {
  if (report.escalations.length === 0) {
    return (
      <Card title="Escalations">
        <Empty title="No escalation rungs fired in this window">
          Either every flag was acknowledged before its first rung, or nothing
          was flagged.
        </Empty>
      </Card>
    );
  }

  return (
    <Card
      title="Escalations by rung and department"
      hint="Counts rungs that actually fired. A ladder stopped by a prompt acknowledgement did not escalate."
    >
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-600">
          <tr>
            <th scope="col" className="py-1">Department</th>
            <th scope="col" className="py-1">Rung</th>
            <th scope="col" className="py-1">Fired</th>
          </tr>
        </thead>
        <tbody>
          {report.escalations.map((row, index) => (
            <tr key={`${row.department_id}-${row.rung}-${index}`} className="border-t border-slate-100">
              <td className="py-2 text-slate-800">
                {row.department_name ?? "Unassigned"}
              </td>
              <td className="py-2 text-slate-700">{row.rung ?? "—"}</td>
              <td className="py-2 text-slate-700">{row.fired}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function PatientContact({ report }: { report: MetricsSummary }) {
  const stats = report.patient_contacts;
  return (
    <Card
      title="Patient contact"
      hint="A notification sent is not a patient reached. The callback rate is the honest number."
    >
      <div className="flex flex-wrap gap-8">
        <Metric label="Notifications sent" value={String(stats.notifications_sent)} />
        <Metric label="Suppressed" value={String(stats.notifications_suppressed)} />
        <Metric label="Failed" value={String(stats.notifications_failed)} />
        <Metric label="Callbacks" value={String(stats.inbound_callbacks)} />
        <Metric
          label="Callback rate"
          value={`${(stats.callback_rate * 100).toFixed(0)}%`}
        />
      </div>
    </Card>
  );
}

function OpenLabFlags({ report }: { report: MetricsSummary }) {
  const stats = report.lab_flags;
  return (
    <Card title="Lab flags open and aged">
      <div className="flex flex-wrap gap-8">
        <Metric label="Open" value={String(stats.open_count)} />
        <Metric
          label="Oldest"
          value={
            stats.oldest_age_hours === null
              ? "—"
              : formatDuration(stats.oldest_age_hours * 3600)
          }
        />
        <Metric label="Overrides" value={String(report.overrides.total)} />
      </div>
    </Card>
  );
}

function PerDoctor({ days }: { days: number }) {
  const { data, isPending, isError, error } = usePerDoctor(days);

  if (isPending) return <Loading />;
  if (isError) return <ErrorState error={error} />;
  if (!data || data.length === 0) {
    return (
      <Card title="Per-doctor acknowledgement times">
        <Empty title="No cases in this window" />
      </Card>
    );
  }

  return (
    <Card
      title="Per-doctor acknowledgement times"
      hint="How long each doctor's flags waited before someone looked. Open cases are the number that matters most."
    >
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-600">
          <tr>
            <th scope="col" className="py-1">Doctor</th>
            <th scope="col" className="py-1">Open</th>
            <th scope="col" className="py-1">Closed</th>
            <th scope="col" className="py-1">p50</th>
            <th scope="col" className="py-1">p90</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.user_id} className="border-t border-slate-100">
              <td className="py-2 text-slate-800">
                {row.full_name}
                <span className="ml-2 font-mono text-xs text-slate-500">
                  {row.employee_code}
                </span>
              </td>
              <td
                className={cn(
                  "py-2",
                  row.open_cases > 0 ? "font-medium text-amber-800" : "text-slate-700",
                )}
              >
                {row.open_cases}
              </td>
              <td className="py-2 text-slate-700">{row.cases_closed}</td>
              <td className="py-2 text-slate-700">
                {row.p50_ack_seconds === null ? "—" : formatDuration(row.p50_ack_seconds)}
              </td>
              <td className="py-2 text-slate-700">
                {row.p90_ack_seconds === null ? "—" : formatDuration(row.p90_ack_seconds)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
