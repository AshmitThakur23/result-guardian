/**
 * Durations, rendered for a ward. Phase 5.2.
 *
 * Two rules, both learned from how these numbers get read at 3am:
 *
 * **Never show a bare number of seconds.** "4500" means nothing; "1h 15m"
 * means something.
 *
 * **Never hide that a countdown has run out.** A negative value means the
 * escalation rung should already have fired and has not — clamping it to
 * "0m" would turn a worker outage into a row that looks merely urgent.
 */

export function formatDuration(seconds: number): string {
  const total = Math.abs(Math.round(seconds));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return "<1m";
}

/** "5h 12m old" — how long a flag has been sitting unattended. */
export function formatAge(seconds: number): string {
  return `${formatDuration(seconds)} old`;
}

export interface Countdown {
  label: string;
  overdue: boolean;
}

/** The next-escalation column. `null` means no rung is scheduled. */
export function formatCountdown(seconds: number | null): Countdown | null {
  if (seconds === null) return null;
  if (seconds < 0) {
    return { label: `Overdue by ${formatDuration(seconds)}`, overdue: true };
  }
  return { label: `in ${formatDuration(seconds)}`, overdue: false };
}
