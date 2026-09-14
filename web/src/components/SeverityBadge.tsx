/**
 * Severity, rendered the same way everywhere it appears.
 *
 * Colour is never the only signal: each badge carries its own word, because a
 * red chip and an amber chip are the same chip to a colour-blind reader and
 * to a printed report.
 */

import { cn } from "../lib/cn";
import type { Severity } from "../api/types";

const TONES: Record<Severity, string> = {
  critical: "border-critical-line bg-critical-subtle text-critical-text",
  follow_up: "border-followup-line bg-followup-subtle text-followup-text",
  normal: "border-normal-line bg-normal-subtle text-normal-text",
};

const LABELS: Record<Severity, string> = {
  critical: "Critical",
  follow_up: "Needs follow-up",
  normal: "Normal",
};

export function SeverityBadge({
  severity,
  className,
}: {
  severity: Severity;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-3 py-1",
        "text-sm font-semibold",
        TONES[severity],
        className,
      )}
    >
      {LABELS[severity]}
    </span>
  );
}

export function severityLabel(severity: Severity): string {
  return LABELS[severity];
}
