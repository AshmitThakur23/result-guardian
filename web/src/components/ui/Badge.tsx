/**
 * A generic status pill.
 *
 * ## Colour is never the only signal
 *
 * Every badge renders a **word**, and the tones are separated on lightness as
 * well as hue, so they stay distinguishable in greyscale, on a projector, and
 * to the ~1 in 12 men with a red/green deficiency. A product that routes
 * critical results cannot encode "critical" in a hue alone.
 *
 * ## ⚠️ For clinical severity, use `components/SeverityBadge` instead
 *
 * That component is the single mapping from a `Severity` to its label, and it
 * says **"Needs follow-up"** — not "Follow-up". This file briefly carried a
 * second `SeverityBadge` with the shorter wording, which would have put two
 * different labels for the same clinical state on two different screens. That
 * is precisely the drift a shared component exists to prevent, so this one is
 * deliberately generic and knows nothing about severity.
 */

import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

type Tone = "critical" | "followup" | "normal" | "info" | "brand";

const TONES: Record<Tone, string> = {
  critical: "bg-critical-subtle text-critical-text ring-critical-line",
  followup: "bg-followup-subtle text-followup-text ring-followup-line",
  normal: "bg-normal-subtle text-normal-text ring-normal-line",
  info: "bg-info-subtle text-info-text ring-info-line",
  brand: "bg-brand-subtle text-brand-text ring-brand/30",
};

export function Badge({
  tone = "info",
  children,
  className,
  dot = false,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  /** A filled dot before the label. Adds a second, non-textual cue. */
  dot?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5",
        "text-xs font-medium ring-1 ring-inset",
        TONES[tone],
        className,
      )}
    >
      {dot ? (
        <span
          aria-hidden="true"
          className="size-1.5 shrink-0 rounded-full bg-current"
        />
      ) : null}
      {children}
    </span>
  );
}
