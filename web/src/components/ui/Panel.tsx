/**
 * The card shape the whole product uses, plus a page header.
 *
 * Both exist to stop the same two patterns being re-typed on fourteen screens
 * with slightly different padding each time. A dense clinical interface is
 * legible because its rhythm is consistent, not because any one card is
 * beautiful.
 */

import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export function Panel({
  title,
  description,
  actions,
  children,
  className,
  as: Tag = "section",
}: {
  title?: ReactNode;
  description?: ReactNode;
  /** Buttons or links that act on this panel, shown on the title row. */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "aside";
}) {
  return (
    <Tag className={cn("rg-panel", className)}>
      {title || actions ? (
        <header
          className={cn(
            "flex flex-wrap items-start justify-between gap-3 px-4 py-3",
            // The divider appears only when there is content beneath it, so an
            // empty panel does not render a stray line.
            children ? "border-b border-line" : null,
          )}
        >
          <div className="min-w-0">
            {title ? (
              <h2 className="text-sm font-semibold text-ink">{title}</h2>
            ) : null}
            {description ? (
              <p className="mt-0.5 text-sm text-ink-muted">{description}</p>
            ) : null}
          </div>
          {actions ? (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          ) : null}
        </header>
      ) : null}
      {children}
    </Tag>
  );
}

/** Standard padding for panel bodies that are not tables. */
export function PanelBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cn("px-4 py-4", className)}>{children}</div>;
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold text-ink">{title}</h1>
        {description ? (
          // `max-w-prose`: a line of explanatory text stretched across a 27"
          // ward monitor is measurably harder to read than one that stops.
          <p className="mt-1 max-w-prose text-sm text-ink-muted">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </header>
  );
}

/**
 * A label/value pair, as used all over the patient and case screens.
 *
 * `dl` rather than a div soup, so a screen reader announces "MRN, 12345" as a
 * pair instead of two unrelated strings.
 */
export function Field({
  label,
  children,
  numeric = false,
}: {
  label: ReactNode;
  children: ReactNode;
  /** Tabular figures, for anything a clinician reads as a number. */
  numeric?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-ink-muted">
        {label}
      </dt>
      <dd className={cn("mt-0.5 text-sm text-ink", numeric && "tabular")}>
        {children}
      </dd>
    </div>
  );
}
