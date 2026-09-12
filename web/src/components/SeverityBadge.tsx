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
  critical: "border-red-300 bg-red-100 text-red-900",
  follow_up: "border-amber-300 bg-amber-100 text-amber-900",
  normal: "border-green-300 bg-green-100 text-green-900",
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
