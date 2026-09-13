/**
 * The header's system-status pill. Phase 5.2.
 *
 *     System status pill in the header, driven by `/api/health`:
 *     `AI: connected` / `AI: offline — core tracking unaffected`
 *
 * **The second half of that string is the important half.** NODE B being
 * offline is a normal operating state (RULE 2), and a bare red "AI: offline"
 * would read as an outage — which is how a ward stops trusting a system that
 * is working perfectly. So the pill says what is *unaffected*, every time.
 *
 * Worker staleness is the opposite case: it is genuinely serious, because a
 * dead worker means timers are not firing, and that is the one failure this
 * product exists to prevent. It gets the red treatment that NODE B does not.
 */

import { useHealth } from "../api/queries5";
import { cn } from "../lib/cn";

const TONES = {
  ok: "border-green-300 bg-green-50 text-green-900",
  info: "border-slate-300 bg-slate-50 text-slate-700",
  warn: "border-amber-300 bg-amber-50 text-amber-900",
  danger: "border-red-300 bg-red-50 text-red-900",
} as const;

export function SystemStatusPill() {
  const { data, isPending, isError } = useHealth();

  if (isPending) {
    return <Pill tone="info" label="Checking…" />;
  }

  if (isError || !data) {
    return (
      <Pill
        tone="warn"
        label="Status unknown"
        title="Could not reach /api/health. Tracking may still be running."
      />
    );
  }

  // Worst first. A stale worker outranks everything: timers are not firing.
  const workerStale = data.degraded_features.includes("worker");
  if (data.db !== "ok") {
    return <Pill tone="danger" label="Database unavailable" />;
  }
  if (workerStale) {
    return (
      <Pill
        tone="danger"
        label="Worker not responding"
        title={
          "Timers may not be firing. This is the failure the system exists to " +
          "prevent — tell the administrator now."
        }
      />
    );
  }

  if (data.llm.reachable) {
    return <Pill tone="ok" label="AI: connected" />;
  }

  return (
    <Pill
      tone="info"
      label="AI: offline — core tracking unaffected"
      title={
        "NODE B is an accelerator. Tracking, timers, escalation and " +
        "notification all run on this machine and are unaffected."
      }
    />
  );
}

function Pill({
  tone,
  label,
  title,
}: {
  tone: keyof typeof TONES;
  label: string;
  title?: string;
}) {
  return (
    <span
      role="status"
      title={title}
      className={cn(
        "inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium",
        TONES[tone],
      )}
    >
      {label}
    </span>
  );
}
