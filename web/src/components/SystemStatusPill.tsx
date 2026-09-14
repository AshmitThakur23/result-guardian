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
import { Badge } from "./ui/Badge";

export function SystemStatusPill() {
  const { data, isPending, isError } = useHealth();

  if (isPending) {
    return <Badge tone="info">Checking…</Badge>;
  }

  if (isError || !data) {
    return (
      <Badge
        tone="followup"
        dot
        className="cursor-help"
        // Title, not a toast: the distinction between "the system is down" and
        // "this browser tab cannot reach it" matters, and guessing wrong in
        // either direction is harmful.
      >
        <span title="Could not reach /api/health. Tracking may still be running.">
          Status unknown
        </span>
      </Badge>
    );
  }

  // Worst first. A stale worker outranks everything: timers are not firing.
  const workerStale = data.degraded_features.includes("worker");
  if (data.db !== "ok") {
    return (
      <Badge tone="critical" dot>
        Database unavailable
      </Badge>
    );
  }
  if (workerStale) {
    return (
      <Badge tone="critical" dot className="cursor-help">
        <span
          title={
            "Timers may not be firing. This is the failure the system exists to " +
            "prevent — tell the administrator now."
          }
        >
          Worker not responding
        </span>
      </Badge>
    );
  }

  if (data.llm.reachable) {
    return (
      <Badge tone="normal" dot>
        AI: connected
      </Badge>
    );
  }

  // `info`, not `warning`. Deliberately the quietest tone in the system.
  return (
    <Badge tone="info" dot className="cursor-help">
      <span
        title={
          "NODE B is an accelerator. Tracking, timers, escalation and " +
          "notification all run on this machine and are unaffected."
        }
      >
        AI: offline — core tracking unaffected
      </span>
    </Badge>
  );
}
