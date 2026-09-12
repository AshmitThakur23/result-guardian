/**
 * The gate's behavioural contract.
 *
 * These tests are written against what a doctor can and cannot do, not against
 * component internals: the product claim is "no post-discharge result is ever
 * lost", and the UI's share of that claim is that there is no way past an
 * outstanding investigation except assigning it or going on the record.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderGate } from "../test/render";
import {
  ENCOUNTER_ID,
  ORDER_A,
  ORDER_B,
  DOCTOR,
  OTHER_DOCTOR,
  encounter,
  installServer,
  problem,
  readiness,
} from "../test/server";

/** Anything that would let a doctor walk past an unassigned result. */
const ESCAPE_HATCH = /\b(skip|not required|dismiss|ignore|remind me later|no results? pending|discharge anyway|mark as done)\b/i;

function futureInput(hoursFromNow: number): string {
  const at = new Date(Date.now() + hoursFromNow * 3600_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(
    at.getHours(),
  )}:${pad(at.getMinutes())}`;
}

/** `datetime-local` cannot be driven by keystrokes in jsdom, so the value is
 *  set the way the browser's own picker would set it. */
function setExpectedBy(orderId: string, value: string) {
  const field = document.getElementById(`expected-${orderId}`) as HTMLInputElement;
  fireEvent.change(field, { target: { value } });
}

function fillBothRows() {
  setExpectedBy(ORDER_A, futureInput(30));
  setExpectedBy(ORDER_B, futureInput(30));
}

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("step 1 — what is outstanding", () => {
  it("names every blocking investigation", async () => {
    installServer();
    renderGate();

    expect(await screen.findByText("Urine Culture")).toBeInTheDocument();
    expect(screen.getByText("HbA1c")).toBeInTheDocument();
  });

  it("shows a red blocking banner naming the count", async () => {
    installServer();
    renderGate();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "2 investigations have no one responsible for their results",
    );
  });

  it("shows the patient and encounter in the header", async () => {
    installServer();
    renderGate();

    expect(await screen.findByRole("heading", { name: /Sunita Rao/ })).toBeInTheDocument();
    expect(screen.getByText("MRN-77021")).toBeInTheDocument();
    expect(screen.getByText("ENC-2026-0042")).toBeInTheDocument();
  });

  it("offers no way to skip, dismiss or discharge anyway", async () => {
    installServer();
    renderGate();
    await screen.findByText("Urine Culture");

    for (const control of [
      ...screen.getAllByRole("button"),
      ...screen.queryAllByRole("link"),
    ]) {
      expect(control.textContent ?? "").not.toMatch(ESCAPE_HATCH);
    }
  });

  it("says plainly that nothing here can be dismissed", async () => {
    installServer();
    renderGate();
    expect(
      await screen.findByText(/Nothing here can be dismissed/i),
    ).toBeInTheDocument();
  });

  it("lists investigations that already have an owner separately", async () => {
    installServer({
      readiness: () =>
        readiness({
          blocking_orders: [],
          can_discharge: true,
          already_contracted: [
            {
              order_id: ORDER_A,
              test_code: "URC",
              test_name: "Urine Culture",
              status: "in_lab",
              contract_id: "ffffffff-ffff-4fff-8fff-000000000001",
              responsible_doctor_id: DOCTOR.id,
              expected_by: new Date(Date.now() + 24 * 3600_000).toISOString(),
            },
          ],
        }),
    });
    renderGate();

    expect(await screen.findByText(/Already assigned \(1\)/)).toBeInTheDocument();
    expect(
      screen.getByText(/Every outstanding investigation has an owner/),
    ).toBeInTheDocument();
  });
});

describe("step 2 — assigning owners and deadlines", () => {
  it("defaults the responsible doctor to the attending doctor", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    const fields = screen.getAllByRole("combobox");
    expect(fields[0]).toHaveValue("Asha Menon");
    expect(fields[1]).toHaveValue("Asha Menon");
  });

  it("defaults expected-by to ordered_at + TAT when that is still in the future", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    const field = document.getElementById(`expected-${ORDER_A}`) as HTMLInputElement;
    expect(field.value).not.toBe("");
    expect(new Date(field.value).getTime()).toBeGreaterThan(Date.now());
  });

  it("leaves expected-by empty, and says why, when the turnaround has already elapsed", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    const field = document.getElementById(`expected-${ORDER_B}`) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(screen.getByText(/No usable default/)).toBeInTheDocument();
  });

  it("enforces the one-hour minimum lead time on the field itself", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    const field = document.getElementById(`expected-${ORDER_A}`) as HTMLInputElement;
    expect(new Date(field.min).getTime()).toBeGreaterThan(Date.now() + 59 * 60_000);
  });

  it("refuses to advance while a row is incomplete, and says which", async () => {
    const user = userEvent.setup();
    const server = installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    // ORDER_B has no date: Review must not proceed.
    await user.click(screen.getByRole("button", { name: "Review" }));

    expect(await screen.findByText(/1 row is incomplete/)).toBeInTheDocument();
    expect(
      screen.getByText("Choose when this result is expected."),
    ).toBeInTheDocument();
    expect(server.postsTo("/discharge")).toHaveLength(0);
  });

  it("applies the first row's doctor and date to every row", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    await user.click(screen.getByRole("button", { name: /Apply first row to all/ }));

    const a = document.getElementById(`expected-${ORDER_A}`) as HTMLInputElement;
    const b = document.getElementById(`expected-${ORDER_B}`) as HTMLInputElement;
    expect(b.value).toBe(a.value);
    expect(b.value).not.toBe("");
  });

  it("searches for a different doctor and selects them by keyboard alone", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    const field = screen.getAllByRole("combobox")[0];
    await user.click(field);
    await user.keyboard("Ravi");

    await screen.findByRole("option", { name: /Ravi Kulkarni/ });
    await user.keyboard("{ArrowDown}{Enter}");

    await waitFor(() => expect(field).toHaveValue("Ravi Kulkarni"));
  });

  it("marks the combobox as expanded only while its list is open", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));

    const field = screen.getAllByRole("combobox")[0];
    expect(field).toHaveAttribute("aria-expanded", "false");
    await user.click(field);
    expect(field).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(field).toHaveAttribute("aria-expanded", "false");
  });
});

describe("step 3 — plain-language review", () => {
  it("reads each assignment back as a sentence", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    await user.click(screen.getByRole("button", { name: "Review" }));

    const review = await screen.findByRole("region", { name: /Step 3/ });
    expect(review).toHaveTextContent(/Dr Asha Menon\s*will review\s*Urine Culture\s*by/);
    expect(review).toHaveTextContent(/Dr Asha Menon\s*will review\s*HbA1c\s*by/);
  });

  it("never prints a raw identifier for a doctor it cannot name", async () => {
    const user = userEvent.setup();
    const unknownDoctor = "99999999-9999-4999-8999-999999999999";
    installServer({
      // Nobody in the directory, and an existing contract owned by someone the
      // screen has never seen: the worst case for name resolution.
      users: () => [],
      readiness: () =>
        readiness({
          blocking_orders: [],
          can_discharge: true,
          already_contracted: [
            {
              order_id: ORDER_A,
              test_code: "URC",
              test_name: "Urine Culture",
              status: "in_lab",
              contract_id: "ffffffff-ffff-4fff-8fff-000000000001",
              responsible_doctor_id: unknownDoctor,
              expected_by: new Date(Date.now() + 24 * 3600_000).toISOString(),
            },
          ],
        }),
    });
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Review and discharge/ }));

    const review = await screen.findByRole("region", { name: /Step 3/ });
    expect(review).toHaveTextContent(/The assigned doctor will review Urine Culture by/);
    expect(review.textContent ?? "").not.toContain(unknownDoctor);
  });
});

describe("confirming the discharge", () => {
  it("creates the contracts, then discharges, in that order", async () => {
    const user = userEvent.setup();
    const server = installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /Confirm discharge/ }));

    await screen.findByText(/Sunita Rao discharged/);

    const posts = server.calls.filter((call) => call.method === "POST");
    expect(posts[0].path).toBe(`/encounters/${ENCOUNTER_ID}/discharge-contracts`);
    expect(posts[1].path).toBe(`/encounters/${ENCOUNTER_ID}/discharge`);
  });

  it("sends an aware timestamp and the chosen doctor for every blocking order", async () => {
    const user = userEvent.setup();
    const server = installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /Confirm discharge/ }));
    await screen.findByText(/Sunita Rao discharged/);

    const body = server.postsTo("/discharge-contracts")[0].body as {
      contracts: { order_id: string; responsible_doctor_id: string; expected_by: string }[];
    };
    expect(body.contracts.map((c) => c.order_id).sort()).toEqual(
      [ORDER_A, ORDER_B].sort(),
    );
    for (const contract of body.contracts) {
      expect(contract.responsible_doctor_id).toBe(DOCTOR.id);
      expect(contract.expected_by).toMatch(/(Z|[+-]\d{2}:\d{2})$/);
    }
  });

  it("sends no body with the discharge itself", async () => {
    const user = userEvent.setup();
    const server = installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /Confirm discharge/ }));
    await screen.findByText(/Sunita Rao discharged/);

    // The server re-derives readiness under a row lock. Anything sent here
    // would only imply the client has a say.
    expect(server.postsTo(`/${ENCOUNTER_ID}/discharge`)[0].body).toBeUndefined();
  });

  it("shows a contract reference for each new contract", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /Confirm discharge/ }));

    await screen.findByText("Contract references");
    expect(screen.getByText("ffffffff-ffff-4fff-8fff-000000000000")).toBeInTheDocument();
    expect(screen.getByText("ffffffff-ffff-4fff-8fff-000000000001")).toBeInTheDocument();
  });

  it("surfaces every validation violation from a 422, not just the first", async () => {
    const user = userEvent.setup();
    installServer({
      createContracts: () =>
        problem(422, "Discharge contracts were not created", {
          violations: [
            { order_id: ORDER_A, code: "doctor_inactive", detail: "Doctor is inactive" },
            { order_id: ORDER_B, code: "expected_by_past", detail: "Date is in the past" },
          ],
        }),
    });
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(screen.getByRole("button", { name: /Confirm discharge/ }));

    expect(await screen.findByText("Doctor is inactive")).toBeInTheDocument();
    expect(screen.getByText("Date is in the past")).toBeInTheDocument();
  });

  it("does not create the contracts twice when only the discharge failed", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    const server = installServer({
      discharge: () => {
        attempt += 1;
        return attempt === 1
          ? problem(409, "Discharge blocked by outstanding investigations")
          : {
              encounter_id: ENCOUNTER_ID,
              status: "discharged",
              discharged_at: new Date().toISOString(),
              opened_cases: [],
            };
      },
    });
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(screen.getByRole("button", { name: /Confirm discharge/ }));

    await screen.findByText(/Discharge blocked by outstanding investigations/);
    expect(screen.getByText(/The assignments were saved/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Confirm discharge/ }));
    await screen.findByText(/Sunita Rao discharged/);

    // A second POST would 409 on UNIQUE (order_id) and strand the doctor.
    expect(server.postsTo("/discharge-contracts")).toHaveLength(1);
  });

  it("skips contract creation entirely when nothing is blocking", async () => {
    const user = userEvent.setup();
    const server = installServer({
      readiness: () => readiness({ blocking_orders: [], can_discharge: true }),
    });
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Review and discharge/ }));
    await user.click(await screen.findByRole("button", { name: /Confirm discharge/ }));
    await screen.findByText(/Sunita Rao discharged/);

    expect(server.postsTo("/discharge-contracts")).toHaveLength(0);
  });
});

describe("the override path", () => {
  it("is a separate red button, never part of the main flow", async () => {
    installServer();
    renderGate();

    const button = await screen.findByRole("button", { name: /Override the gate/ });
    expect(button.className).toMatch(/bg-red-700/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("demands a reason code, twenty characters of explanation and an identity", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Override the gate/ }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: /Override and discharge/ });

    expect(confirm).toBeDisabled();

    await user.selectOptions(
      within(dialog).getByLabelText("Reason"),
      "clinical_urgency",
    );
    await user.type(within(dialog).getByLabelText("What happened?"), "too short");
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByText(/At least 20 characters/)).toBeInTheDocument();

    await user.clear(within(dialog).getByLabelText("What happened?"));
    await user.type(
      within(dialog).getByLabelText("What happened?"),
      "Patient moved to ICU at another facility immediately.",
    );
    expect(confirm).toBeDisabled(); // still no overriding doctor

    await user.click(within(dialog).getByLabelText("Overriding doctor"));
    await user.keyboard("Asha");
    await user.click(await within(dialog).findByRole("option", { name: /Asha Menon/ }));

    await waitFor(() => expect(confirm).toBeEnabled());
  });

  it("states that nothing stops being tracked", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Override the gate/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Nothing stops being tracked")).toBeInTheDocument();
    expect(within(dialog).getByText(/An override is not a dismissal/)).toBeInTheDocument();
  });

  it("posts the override and reports what was flagged to the unit head", async () => {
    const user = userEvent.setup();
    const server = installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Override the gate/ }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText("Reason"), "patient_lama");
    await user.type(
      within(dialog).getByLabelText("What happened?"),
      "Patient left the ward against advice before results returned.",
    );
    await user.click(within(dialog).getByLabelText("Overriding doctor"));
    await user.keyboard("Asha");
    await user.click(await within(dialog).findByRole("option", { name: /Asha Menon/ }));
    await user.click(within(dialog).getByRole("button", { name: /Override and discharge/ }));

    await screen.findByText(/discharged with the gate overridden/);
    expect(screen.getByText(/Bypassed and flagged to the unit head/)).toBeInTheDocument();
    expect(
      screen.getByText(/Urine Culture\s*—\s*now owned by\s*Dr Meera Iyer/),
    ).toBeInTheDocument();

    const body = server.postsTo("/discharge-overrides")[0].body as {
      reason_code: string;
      reason_text: string;
      overridden_by: string;
    };
    expect(body.reason_code).toBe("patient_lama");
    expect(body.reason_text.trim().length).toBeGreaterThanOrEqual(20);
    expect(body.overridden_by).toBe(DOCTOR.id);
  });

  it("can be abandoned, returning the doctor to the assignment flow", async () => {
    const user = userEvent.setup();
    const server = installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Override the gate/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /Go back and assign/ }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(server.postsTo("/discharge-overrides")).toHaveLength(0);
  });
});

describe("draft persistence across a refresh", () => {
  it("restores the step and the assignments", async () => {
    const user = userEvent.setup();
    installServer();
    const first = renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    const field = screen.getAllByRole("combobox")[0];
    await user.click(field);
    await user.keyboard("Ravi");
    await user.click(await screen.findByRole("option", { name: /Ravi Kulkarni/ }));
    fillBothRows();

    first.unmount(); // stands in for the refresh

    renderGate();
    await screen.findByRole("region", { name: /Step 2/ });
    await waitFor(() =>
      expect(screen.getAllByRole("combobox")[0]).toHaveValue("Ravi Kulkarni"),
    );
    expect(
      (document.getElementById(`expected-${ORDER_B}`) as HTMLInputElement).value,
    ).not.toBe("");
  });

  it("clears the draft once the discharge succeeds", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();

    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    expect(sessionStorage.getItem(`rg.discharge-draft.${ENCOUNTER_ID}`)).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /Confirm discharge/ }));
    await screen.findByText(/Sunita Rao discharged/);

    expect(sessionStorage.getItem(`rg.discharge-draft.${ENCOUNTER_ID}`)).toBeNull();
  });

  it("drops a saved assignment whose order stopped blocking", async () => {
    const user = userEvent.setup();
    installServer();
    const first = renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    fillBothRows();
    first.unmount();

    vi.unstubAllGlobals();
    const server = installServer({
      readiness: () =>
        readiness({
          blocking_orders: readiness().blocking_orders.filter(
            (order) => order.order_id === ORDER_A,
          ),
        }),
    });

    renderGate();
    await screen.findByRole("region", { name: /Step 2/ });
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /Confirm discharge/ }));
    await screen.findByText(/Sunita Rao discharged/);

    const body = server.postsTo("/discharge-contracts")[0].body as {
      contracts: { order_id: string }[];
    };
    expect(body.contracts.map((c) => c.order_id)).toEqual([ORDER_A]);
  });
});

describe("degradation", () => {
  it("reports a 404 encounter without pretending the gate passed", async () => {
    installServer({ readiness: () => problem(404, "Encounter not found") });
    renderGate();

    expect(await screen.findByText("Encounter not found")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm discharge/ })).toBeNull();
  });

  it("refuses a malformed encounter id before calling the server at all", async () => {
    const server = installServer();
    renderGate("not-a-uuid");

    expect(
      await screen.findByText("That is not a valid encounter reference"),
    ).toBeInTheDocument();
    expect(server.calls).toHaveLength(0);
  });

  it("says the network is unreachable rather than showing a parser error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderGate();

    expect(
      await screen.findByText("Cannot reach the Result Guardian server"),
    ).toBeInTheDocument();
  });

  it("notes when the gate does not apply to this encounter type", async () => {
    installServer({
      encounter: () => encounter({ type: "opd" }),
      readiness: () =>
        readiness({ encounter_type: "opd", gate_applies: false, can_discharge: true }),
    });
    renderGate();

    expect(
      await screen.findByText(/does not apply to this encounter/),
    ).toBeInTheDocument();
  });

  it("does not offer to discharge an encounter that already was", async () => {
    installServer({
      encounter: () =>
        encounter({ status: "discharged", discharged_at: new Date().toISOString() }),
      readiness: () => readiness({ blocking_orders: [], can_discharge: false }),
    });
    renderGate();

    expect(
      await screen.findByText("This encounter has already been discharged"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm discharge/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Override the gate/ })).toBeNull();
  });
});

describe("availability is not claimed", () => {
  it("shows no on-duty or availability badge anywhere", async () => {
    const user = userEvent.setup();
    installServer();
    renderGate();
    await user.click(await screen.findByRole("button", { name: /Assign responsibility/ }));
    await user.click(screen.getAllByRole("combobox")[0]);
    await screen.findByRole("option", { name: /Asha Menon/ });

    // duty_roster / user_absences are Phase 4.1. Until they exist the gate
    // must not imply it knows who is on duty.
    expect(document.body.textContent ?? "").not.toMatch(
      /\b(on duty|off duty|available now|unavailable|on leave)\b/i,
    );
    expect(OTHER_DOCTOR.is_active).toBe(true);
  });
});

describe("a doctor outside the first page of search results", () => {
  /**
   * Regression, found by the Phase 1.8 browser run.
   *
   * `GET /api/users?role=doctor&limit=20` returns one page, ordered by name.
   * On a ward with more doctors than that, a responsible doctor chosen earlier
   * and restored from a draft is not in the page the field happens to be
   * showing — and the field rendered **completely empty**, placeholder and
   * all, while the form still held the id and let the doctor continue.
   *
   * A blank "responsible doctor" that the gate nonetheless accepts is the
   * worst of both: it reads as unassigned and behaves as assigned.
   */
  const MANY_DOCTORS = [
    ...Array.from({ length: 24 }, (_, i) => ({
      id: `4${String(i).padStart(7, "0")}-0000-4000-8000-000000000000`,
      employee_code: `D-2${String(i).padStart(3, "0")}`,
      // Sorts before "Ravi", so Ravi falls off the end of a 20-row page.
      full_name: `Anita Crowd ${String(i).padStart(2, "0")}`,
      role: "doctor",
      is_active: true,
      department_id: null,
    })),
    DOCTOR,
    OTHER_DOCTOR,
  ];

  function seedDraftChoosing(doctorId: string) {
    sessionStorage.setItem(
      `rg.discharge-draft.${ENCOUNTER_ID}`,
      JSON.stringify({
        version: 1,
        encounter_id: ENCOUNTER_ID,
        step: 2,
        assignments: {
          [ORDER_A]: {
            responsible_doctor_id: doctorId,
            expected_by_input: "2026-12-01T18:00",
          },
          [ORDER_B]: {
            responsible_doctor_id: doctorId,
            expected_by_input: "2026-12-01T18:00",
          },
        },
        saved_at: new Date().toISOString(),
      }),
    );
  }

  it("still shows the doctor's name after a restore", async () => {
    installServer({ users: () => MANY_DOCTORS });
    seedDraftChoosing(OTHER_DOCTOR.id);
    renderGate();

    await screen.findByRole("region", { name: /Step 2/ });
    await waitFor(() =>
      expect(screen.getAllByRole("combobox")[0]).toHaveValue(
        OTHER_DOCTOR.full_name,
      ),
    );
  });

  it("never renders an empty field for an assignment that is actually set", async () => {
    installServer({ users: () => MANY_DOCTORS });
    seedDraftChoosing(OTHER_DOCTOR.id);
    renderGate();

    await screen.findByRole("region", { name: /Step 2/ });
    await waitFor(() =>
      expect(screen.getAllByRole("combobox")[0]).not.toHaveValue(""),
    );
  });
});

describe("a doctor the screen cannot name at all", () => {
  it("still reads as a selection, never as an empty field", async () => {
    // Larger than the directory page: the id resolves nowhere. The field must
    // not imply "nothing chosen" while the form holds a doctor.
    installServer({ users: () => [] });
    sessionStorage.setItem(
      `rg.discharge-draft.${ENCOUNTER_ID}`,
      JSON.stringify({
        version: 1,
        encounter_id: ENCOUNTER_ID,
        step: 2,
        assignments: {
          [ORDER_A]: {
            responsible_doctor_id: "99999999-9999-4999-8999-999999999999",
            expected_by_input: "2026-12-01T18:00",
          },
        },
        saved_at: new Date().toISOString(),
      }),
    );
    renderGate();

    await screen.findByRole("region", { name: /Step 2/ });
    const field = screen.getAllByRole("combobox")[0];
    await waitFor(() => expect(field).not.toHaveValue(""));
    // And never a raw identifier in front of a doctor.
    expect((field as HTMLInputElement).value).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    );
  });
});
