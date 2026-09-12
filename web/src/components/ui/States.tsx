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
import type { ApiError } from "../../api/client";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p role="status" className="py-8 text-center text-sm text-slate-600">
      {label}
    </p>
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
    <div className="rounded-md border border-dashed border-slate-300 bg-white px-4 py-8 text-center">
      <p className="text-sm font-medium text-slate-800">{title}</p>
      {children ? <div className="mt-1 text-sm text-slate-600">{children}</div> : null}
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
