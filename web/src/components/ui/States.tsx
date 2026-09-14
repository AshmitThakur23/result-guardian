/**
 * The four states every screen in this product has to handle.
 *
 * Pulled out because Phase 1.5 added three screens at once and the alternative
 * was three slightly different spellings of "Loading…" — and, worse, three
 * different ideas of what an error looks like. A clinician who cannot tell a
 * failed request from an empty result will assume the empty result.
 */

import type { ReactNode } from "react";

import { Banner } from "./Banner";
import { Button } from "./Button";
import { cn } from "../../lib/cn";
import type { ApiError } from "../../api/client";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p
      role="status"
      className="flex items-center justify-center gap-2 py-8 text-sm text-ink-muted"
    >
      <Spinner />
      {label}
    </p>
  );
}

/**
 * A spinner, not a progress bar: we genuinely do not know how long an
 * extraction will take, and a bar that stalls at 90% is a lie.
 *
 * `aria-hidden` because the surrounding `role="status"` already announces the
 * label — otherwise a screen reader reads the decoration too.
 */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block size-3.5 shrink-0 animate-spin rounded-full",
        "border-2 border-line-strong border-t-brand",
        className,
      )}
    />
  );
}

/**
 * A shaped placeholder for content that is on its way.
 *
 * Used only where the final shape is known — a table of a known column count,
 * say — because a skeleton that does not match what arrives is worse than a
 * spinner: the eye settles on a layout that then jumps.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("block animate-pulse rounded bg-surface-sunken", className)}
    />
  );
}

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong bg-surface px-4 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children ? <div className="mt-1 text-sm text-ink-muted">{children}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  fallbackTitle = "Something went wrong",
}: {
  error: unknown;
  onRetry?: () => void;
  fallbackTitle?: string;
}) {
  // Narrowed structurally rather than with instanceof: the error may have
  // crossed a query-cache boundary, and a failed request must never render as
  // an empty list.
  const problem =
    typeof error === "object" && error !== null && "problem" in error
      ? (error as ApiError).problem
      : null;

  return (
    <Banner tone="danger" title={problem?.title ?? fallbackTitle}>
      {problem?.detail ? <p>{problem.detail}</p> : null}
      {onRetry ? (
        <Button variant="secondary" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </Banner>
  );
}
