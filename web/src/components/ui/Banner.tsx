/**
 * A block of text the user must not scroll past.
 *
 * The `role` split is the important part and is not cosmetic: `alert` is
 * announced immediately and interrupts, `status` is announced politely at the
 * next pause. A doctor using a screen reader must be told the discharge is
 * blocked without having to go looking for the reason — but must *not* be
 * interrupted mid-sentence to be told a report uploaded fine.
 *
 * `info` is intentionally neutral rather than blue. It carries the "AI offline
 * — core tracking unaffected" message, which describes a **normal operating
 * state** under RULE 2. Styling that as an alert is how a ward learns to
 * distrust a system that is working perfectly.
 */

import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

type Tone = "danger" | "warning" | "info" | "success";

const TONES: Record<Tone, string> = {
  danger: "border-critical-line bg-critical-subtle text-critical-text",
  warning: "border-followup-line bg-followup-subtle text-followup-text",
  info: "border-info-line bg-info-subtle text-info-text",
  success: "border-normal-line bg-normal-subtle text-normal-text",
};

/** A 3px rail down the leading edge — readable in greyscale, unlike fill alone. */
const RAILS: Record<Tone, string> = {
  danger: "before:bg-critical",
  warning: "before:bg-followup",
  info: "before:bg-line-strong",
  success: "before:bg-normal",
};

export function Banner({
  tone,
  title,
  children,
  className,
}: {
  tone: Tone;
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      // `alert` on the blocking and error banners: a doctor using a screen
      // reader must be told the discharge is blocked without having to go
      // looking for the reason.
      role={tone === "danger" || tone === "warning" ? "alert" : "status"}
      className={cn(
        "relative overflow-hidden rounded border py-3 pl-5 pr-4",
        // The rail. Colour is never the only signal, so severity survives a
        // greyscale print-out and a red/green deficiency.
        "before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:content-['']",
        TONES[tone],
        RAILS[tone],
        className,
      )}
    >
      <p className="font-semibold">{title}</p>
      {children ? <div className="mt-1 text-sm opacity-90">{children}</div> : null}
    </div>
  );
}
