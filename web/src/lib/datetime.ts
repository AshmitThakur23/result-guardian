/**
 * Timestamps cross three representations on this screen and getting the edges
 * wrong is a deadline that fires at the wrong hour:
 *
 *   wire      ISO-8601 with an offset, UTC  ("2026-03-14T12:30:00Z")
 *   input     `datetime-local` value, naive local ("2026-03-14T18:00")
 *   display   IST-shaped local text          ("14 Mar 2026, 6:00 PM")
 *
 * The backend types `expected_by` as `AwareDatetime` and rejects a naive
 * value, so the wire direction always goes through `Date.toISOString()`.
 */

import { format, isValid, parseISO } from "date-fns";

/** Build plan 1.4: an expected-by date must be at least an hour out. */
export const MIN_LEAD_MS = 60 * 60 * 1000;

/** Mirrors `MAX_CONTRACT_HORIZON_DAYS` in `api/app/schemas/discharge.py`. */
export const MAX_HORIZON_DAYS = 30;
export const MAX_HORIZON_MS = MAX_HORIZON_DAYS * 24 * 60 * 60 * 1000;

/** ISO on the wire → the naive local string an `<input type="datetime-local">`
 *  expects. Returns "" for anything unparseable so a bad value clears the
 *  field rather than wedging it. */
export function isoToInputValue(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = parseISO(iso);
  return isValid(date) ? format(date, "yyyy-MM-dd'T'HH:mm") : "";
}

/** The naive local string from the input → ISO with an offset, for the wire. */
export function inputValueToIso(value: string): string | null {
  if (!value) return null;
  // `new Date("2026-03-14T18:00")` is interpreted in the browser's zone, which
  // is what the doctor typed. toISOString then makes the offset explicit.
  const date = new Date(value);
  return isValid(date) ? date.toISOString() : null;
}

/** The `min` attribute for the expected-by field: now + one hour, local. */
export function minExpectedByInputValue(now: Date = new Date()): string {
  return format(new Date(now.getTime() + MIN_LEAD_MS), "yyyy-MM-dd'T'HH:mm");
}

export function maxExpectedByInputValue(now: Date = new Date()): string {
  return format(new Date(now.getTime() + MAX_HORIZON_MS), "yyyy-MM-dd'T'HH:mm");
}

/** "14 Mar 2026, 6:00 PM" — the form the review step reads aloud. */
export function formatDeadline(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseISO(iso);
  return isValid(date) ? format(date, "d MMM yyyy, h:mm a") : "—";
}

/** "14 Mar, 6:00 PM" — no year, for dense rows. */
export function formatShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseISO(iso);
  return isValid(date) ? format(date, "d MMM, h:mm a") : "—";
}

/** How long an order has been outstanding, in plain words. */
export function outstandingFor(orderedAt: string, now: Date = new Date()): string {
  const ordered = parseISO(orderedAt);
  if (!isValid(ordered)) return "unknown";
  const hours = Math.floor((now.getTime() - ordered.getTime()) / (60 * 60 * 1000));
  if (hours < 1) return "less than an hour";
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"}`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

export type ExpectedByProblem = "missing" | "unparseable" | "too_soon" | "too_far";

/**
 * Client-side mirror of the server's expected_by rules. The server is still
 * the authority -- this exists so a doctor is told at the field, not after
 * submitting the whole batch.
 */
export function checkExpectedBy(
  value: string,
  now: Date = new Date(),
): ExpectedByProblem | null {
  if (!value.trim()) return "missing";
  const iso = inputValueToIso(value);
  if (iso === null) return "unparseable";
  const at = new Date(iso).getTime();
  if (at < now.getTime() + MIN_LEAD_MS) return "too_soon";
  if (at > now.getTime() + MAX_HORIZON_MS) return "too_far";
  return null;
}

export const EXPECTED_BY_MESSAGES: Record<ExpectedByProblem, string> = {
  missing: "Choose when this result is expected.",
  unparseable: "That is not a valid date and time.",
  too_soon: "Must be at least 1 hour from now.",
  too_far: `Must be within ${MAX_HORIZON_DAYS} days.`,
};
