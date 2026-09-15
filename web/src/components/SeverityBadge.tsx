/**
 * Severity, rendered the same way everywhere it appears.
 *
 * ## Five redundant channels, because one of them is always missing
 *
 * This badge is the only thing on a worklist row that says "a doctor must act".
 * It gets read from two metres away, over a clerk's shoulder, on a projector in
 * a handover meeting, on a monochrome ward printout, and by the ~1 in 12 men
 * with a red/green deficiency. Each of those takes a different channel away, so
 * severity is encoded five times over and no single channel is load-bearing:
 *
 * 1. **Fill weight** — critical is a *solid* fill; everything else is a tint.
 * 2. **Icon silhouette** — ISO 7010 shape language: octagon = stop, triangle =
 *    warn, circle = safe. The outline resolves before the interior does, so the
 *    shape is still legible at the distance where the glyph inside is mush.
 * 3. **Word case and weight** — `CRITICAL` uppercase and tracked, "Needs
 *    follow-up" sentence case, "Normal" lightest of the three. Word *shape*
 *    differs even when the word itself is too small to read.
 * 4. **Hue** — the channel that was previously doing all of the work alone.
 * 5. **The ring** on the tinted tones, which gives the non-critical badges a
 *    defined edge against the surface and satisfies WCAG 1.4.11 (non-text
 *    contrast) without relying on the fill.
 *
 * ## Why solid fill is reserved for critical, and for nothing else
 *
 * Before this change the three tones were three `-subtle` backgrounds, and the
 * critical and normal fills sat within about four points of lightness of one
 * another. Convert that to greyscale — which is what a monochrome printer, a
 * washed-out projector, and human peripheral vision all effectively do — and
 * CRITICAL and NORMAL become the same chip. Hue is the first channel to go, and
 * it is also the one channel a colour-blind reader never had.
 *
 * **Lightness is the channel that survives all of it.** A solid, dark, saturated
 * fill against light text is a different *mass* on the screen, not a different
 * colour, so it still reads as "not like the others" in greyscale, at two
 * metres, and in the corner of an eye. That makes it the strongest signal the
 * medium has, and the strongest signal is spent on the one state that means a
 * patient may come to harm. Giving follow-up a solid fill too would spend it
 * twice and leave critical with nothing louder to say.
 *
 * ## This is not a conflict with `ui/Button.tsx`
 *
 * `Button.tsx` reserves `bg-critical` for its `danger` variant and says
 * "nothing else on this screen is red-filled". That rule is about **controls** —
 * it stops the irreversible override action from looking like Save. A badge is
 * not a control: it reports state, it is not clickable, and nobody can fire it
 * by accident. The two rules are about different things and both still hold:
 * exactly one red-filled *control*, and exactly one red-filled *status*.
 *
 * ## The labels are a contract
 *
 * `LABELS` is the single mapping from a `Severity` to its wording, and the
 * strings are asserted verbatim in `pages/Worklist.test.tsx` ("Needs
 * follow-up") and `pages/CaseDetail.test.tsx` ("Normal"). The uppercasing of
 * CRITICAL is done in CSS, deliberately: the DOM text stays "Critical", so the
 * accessible name and any copy-paste out of the browser keep sentence case.
 */

import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import type { Severity } from "../api/types";

/**
 * Fill, text and edge. Only critical is solid; the other two are a tint plus a
 * ring, which keeps every tone the same height (a `border` would not — hence
 * `ring-1 ring-inset`, which paints inside the box and costs no layout).
 */
const TONES: Record<Severity, string> = {
  critical: "bg-critical text-ink-inverse",
  follow_up:
    "bg-followup-subtle text-followup-text ring-1 ring-inset ring-followup-line",
  normal: "bg-normal-subtle text-normal-text ring-1 ring-inset ring-normal-line",
};

/** Typographic weight of the word itself — channel 3. */
const WORD: Record<Severity, string> = {
  critical: "uppercase tracking-wide font-semibold",
  follow_up: "font-medium",
  normal: "font-normal",
};

const LABELS: Record<Severity, string> = {
  critical: "Critical",
  follow_up: "Needs follow-up",
  normal: "Normal",
};

type Size = "sm" | "lg";

/**
 * `sm` keeps the footprint this badge has always had in a worklist row; `lg`
 * is for the case-detail header, where the badge is the page's subject rather
 * than one cell in a scan. Padding is shared so a row never reflows when the
 * severity changes — the size difference is carried by the icon and the gap.
 */
const SIZES: Record<Size, string> = {
  sm: "px-3 py-1 text-sm gap-1.5",
  lg: "px-3 py-1 text-sm gap-2",
};

const ICON_SIZES: Record<Size, string> = {
  sm: "size-4 shrink-0",
  lg: "size-5 shrink-0",
};

/**
 * **Filled octagon with `!`** — the stop sign. The only solid silhouette in the
 * set, so it is the one that survives being too small to read. The exclamation
 * is punched *out* of the fill with `evenodd`, so the badge background shows
 * through and the glyph needs no second colour token.
 */
function CriticalIcon({ className }: { className: string }): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      fillRule="evenodd"
      clipRule="evenodd"
    >
      <path d="M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86L7.86 2Zm3.04 4.4h2.2v7.2h-2.2V6.4Zm0 9h2.2v2.2h-2.2v-2.2Z" />
    </svg>
  );
}

/** **Outlined triangle with `!`** — warn. Hollow, so it cannot be mistaken for the octagon at a glance. */
function FollowUpIcon({ className }: { className: string }): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3.4 21.5 19.8H2.5L12 3.4Z" />
      <path d="M12 9.6v4" />
      <path d="M12 16.8h.01" />
    </svg>
  );
}

/** **Outlined circle with a check** — safe. No corners at all, which is the widest possible separation from the octagon. */
function NormalIcon({ className }: { className: string }): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="8.8" />
      <path d="m8 12.2 2.7 2.7L16 9.4" />
    </svg>
  );
}

const ICONS: Record<Severity, (props: { className: string }) => ReactElement> = {
  critical: CriticalIcon,
  follow_up: FollowUpIcon,
  normal: NormalIcon,
};

export function SeverityBadge({
  severity,
  size = "sm",
  className,
}: {
  severity: Severity;
  /** `lg` for the case-detail header; `sm` (default) for rows and panels. */
  size?: Size;
  className?: string;
}) {
  const Icon = ICONS[severity];

  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-full",
        SIZES[size],
        TONES[severity],
        className,
      )}
    >
      {/* Decorative: the word below already carries the meaning, so announcing
          the icon too would make a screen reader say "critical" twice. */}
      <Icon className={ICON_SIZES[size]} />
      <span className={WORD[severity]}>{LABELS[severity]}</span>
    </span>
  );
}

export function severityLabel(severity: Severity): string {
  return LABELS[severity];
}
