/**
 * Draft persistence for the discharge gate.
 *
 * A doctor half-way through assigning owners who refreshes, or whose browser
 * is closed by a ward PC's session policy, must not lose the work -- the
 * realistic alternative is that they give up and press the override button.
 *
 * `sessionStorage`, not `localStorage`: the draft is scoped to one tab and
 * dies with it. A ward terminal is shared, and a half-filled discharge for
 * another doctor's patient surviving until someone clears site data is a
 * wrong-patient hazard, not a convenience.
 *
 * Nothing clinical is stored -- only order ids, doctor ids and chosen dates.
 * No patient name, no MRN, no test result.
 */

import { z } from "zod";

const STORAGE_PREFIX = "rg.discharge-draft.";

/** Bumped whenever the shape below changes, so an old draft is discarded
 *  rather than half-read into a new UI. */
const DRAFT_VERSION = 1;

const assignmentSchema = z.object({
  responsible_doctor_id: z.string().uuid().nullable(),
  /** Naive local `datetime-local` value, exactly as typed. */
  expected_by_input: z.string(),
  note: z.string().max(2000).optional(),
});

const draftSchema = z.object({
  version: z.literal(DRAFT_VERSION),
  encounter_id: z.string().uuid(),
  step: z.union([z.literal(1), z.literal(2), z.literal(3)]),
  /** Keyed by order_id. */
  assignments: z.record(z.string().uuid(), assignmentSchema),
  saved_at: z.string(),
});

export type Assignment = z.infer<typeof assignmentSchema>;
export type Draft = z.infer<typeof draftSchema>;

export function emptyAssignment(): Assignment {
  return { responsible_doctor_id: null, expected_by_input: "" };
}

function keyFor(encounterId: string): string {
  return `${STORAGE_PREFIX}${encounterId}`;
}

/**
 * Read the draft for this encounter, or null.
 *
 * Anything that does not validate is dropped *and deleted*: a corrupt or
 * hand-edited draft must never be able to put a doctor id next to an order it
 * was not chosen for. The cost of discarding is retyping; the cost of trusting
 * it is the wrong doctor owning a result.
 */
export function loadDraft(encounterId: string): Draft | null {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(keyFor(encounterId));
  } catch {
    // Private mode, or storage disabled by policy. The gate still works, it
    // just does not survive a refresh.
    return null;
  }
  if (!raw) return null;

  try {
    const parsed = draftSchema.safeParse(JSON.parse(raw));
    if (!parsed.success || parsed.data.encounter_id !== encounterId) {
      clearDraft(encounterId);
      return null;
    }
    return parsed.data;
  } catch {
    clearDraft(encounterId);
    return null;
  }
}

export function saveDraft(
  encounterId: string,
  step: Draft["step"],
  assignments: Record<string, Assignment>,
): void {
  const draft: Draft = {
    version: DRAFT_VERSION,
    encounter_id: encounterId,
    step,
    assignments,
    saved_at: new Date().toISOString(),
  };
  try {
    sessionStorage.setItem(keyFor(encounterId), JSON.stringify(draft));
  } catch {
    // Quota or policy. Losing persistence is survivable; throwing here would
    // take the whole gate screen down with it.
  }
}

/** Called on success, and whenever a draft is found to be untrustworthy. */
export function clearDraft(encounterId: string): void {
  try {
    sessionStorage.removeItem(keyFor(encounterId));
  } catch {
    /* nothing we can do, and nothing that should break the screen */
  }
}

/**
 * Keep only the orders that are still blocking.
 *
 * Between saving a draft and reloading it, a result may have arrived or a
 * colleague may have contracted an order. Assignments for orders that are no
 * longer outstanding are dropped rather than re-submitted -- the server would
 * refuse them with a 409 anyway, and all-or-nothing means one stale row would
 * take the entire batch down.
 */
export function reconcile(
  assignments: Record<string, Assignment>,
  blockingOrderIds: readonly string[],
): Record<string, Assignment> {
  const next: Record<string, Assignment> = {};
  for (const orderId of blockingOrderIds) {
    next[orderId] = assignments[orderId] ?? emptyAssignment();
  }
  // Anything keyed to an order that is no longer blocking is simply not
  // carried over.
  return next;
}
