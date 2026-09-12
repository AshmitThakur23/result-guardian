import { beforeEach, describe, expect, it } from "vitest";

import { clearDraft, emptyAssignment, loadDraft, reconcile, saveDraft } from "./draft";

const ENCOUNTER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const OTHER_ENCOUNTER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const ORDER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const DOCTOR = "11111111-1111-4111-8111-111111111111";

const KEY = `rg.discharge-draft.${ENCOUNTER}`;

function assignment() {
  return {
    ...emptyAssignment(),
    responsible_doctor_id: DOCTOR,
    expected_by_input: "2026-03-20T18:00",
  };
}

describe("draft persistence", () => {
  beforeEach(() => sessionStorage.clear());

  it("round-trips a draft", () => {
    saveDraft(ENCOUNTER, 2, { [ORDER]: assignment() });
    const draft = loadDraft(ENCOUNTER);
    expect(draft?.step).toBe(2);
    expect(draft?.assignments[ORDER].responsible_doctor_id).toBe(DOCTOR);
  });

  it("is scoped per encounter", () => {
    saveDraft(ENCOUNTER, 2, { [ORDER]: assignment() });
    expect(loadDraft(OTHER_ENCOUNTER)).toBeNull();
  });

  it("uses sessionStorage, so it dies with the tab", () => {
    saveDraft(ENCOUNTER, 2, { [ORDER]: assignment() });
    expect(sessionStorage.getItem(KEY)).not.toBeNull();
    // A shared ward terminal must not carry one doctor's half-finished
    // discharge into the next person's session.
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("clears on demand", () => {
    saveDraft(ENCOUNTER, 2, { [ORDER]: assignment() });
    clearDraft(ENCOUNTER);
    expect(loadDraft(ENCOUNTER)).toBeNull();
  });

  it("discards and deletes a corrupt draft rather than half-reading it", () => {
    sessionStorage.setItem(KEY, "{not json");
    expect(loadDraft(ENCOUNTER)).toBeNull();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("discards a draft whose shape does not validate", () => {
    sessionStorage.setItem(
      KEY,
      JSON.stringify({ version: 1, encounter_id: ENCOUNTER, step: 9, assignments: {} }),
    );
    expect(loadDraft(ENCOUNTER)).toBeNull();
  });

  it("refuses a draft stored under one encounter but claiming another", () => {
    // The hazard this guards: a doctor id chosen for one patient's order
    // silently reappearing against a different patient's.
    sessionStorage.setItem(
      KEY,
      JSON.stringify({
        version: 1,
        encounter_id: OTHER_ENCOUNTER,
        step: 2,
        assignments: {},
        saved_at: new Date().toISOString(),
      }),
    );
    expect(loadDraft(ENCOUNTER)).toBeNull();
  });

  it("discards a draft written by an older version of the shape", () => {
    sessionStorage.setItem(
      KEY,
      JSON.stringify({
        version: 0,
        encounter_id: ENCOUNTER,
        step: 2,
        assignments: {},
        saved_at: new Date().toISOString(),
      }),
    );
    expect(loadDraft(ENCOUNTER)).toBeNull();
  });
});

describe("reconcile against live readiness", () => {
  it("keeps assignments for orders that are still blocking", () => {
    const next = reconcile({ [ORDER]: assignment() }, [ORDER]);
    expect(next[ORDER].responsible_doctor_id).toBe(DOCTOR);
  });

  it("drops assignments for orders that stopped blocking", () => {
    // Submitting a stale order would 409, and the batch is all-or-nothing --
    // one stale row would take every good one down with it.
    const next = reconcile({ [ORDER]: assignment() }, []);
    expect(next).toEqual({});
  });

  it("creates a blank assignment for an order that appeared since", () => {
    const fresh = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const next = reconcile({}, [fresh]);
    expect(next[fresh]).toEqual(emptyAssignment());
  });
});
