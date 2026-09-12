/**
 * Phase 1.5 — encounter detail, manual order entry, medication entry.
 *
 * The rule these tests protect: the screen may *report* the gate's answer but
 * must never be able to change it. Hiding the add-order button on a discharged
 * encounter is a courtesy; the server refuses the insert under a row lock
 * regardless, and a test here asserts the screen never claims otherwise.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp } from "../test/render15";
import {
  ENCOUNTER_ID,
  encounterFullDetail,
  installPhase15Server,
  problem,
} from "../test/server";

afterEach(() => vi.unstubAllGlobals());

const DETAIL_PATH = `/encounters/${ENCOUNTER_ID}`;

describe("encounter detail", () => {
  it("shows the encounter, patient and attending doctor", async () => {
    installPhase15Server();
    renderApp(DETAIL_PATH);

    expect(
      await screen.findByRole("heading", { name: /ENC-2026-0042/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Sunita Rao · MRN-77021/)).toBeInTheDocument();
    expect(screen.getByText("Asha Menon")).toBeInTheDocument();
    expect(screen.getByText(/Ward 3/)).toBeInTheDocument();
  });

  it("separates outstanding investigations from resulted ones", async () => {
    installPhase15Server();
    renderApp(DETAIL_PATH);

    await screen.findByText("Urine Culture");
    expect(screen.getByText("Outstanding (1)")).toBeInTheDocument();
    expect(screen.getByText("Resulted or closed (1)")).toBeInTheDocument();
  });

  it("says who is responsible, or that nobody is", async () => {
    installPhase15Server();
    renderApp(DETAIL_PATH);

    await screen.findByText("Urine Culture");
    expect(screen.getByText("No one yet")).toBeInTheDocument();
  });

  it("names the responsible doctor once a contract exists", async () => {
    const detail = encounterFullDetail();
    detail.orders[0] = {
      ...detail.orders[0],
      contract_id: "ffffffff-ffff-4fff-8fff-000000000001",
      responsible_doctor_id: "11111111-1111-4111-8111-111111111111",
      responsible_doctor_name: "Asha Menon",
      expected_by: new Date(Date.now() + 24 * 3600_000).toISOString(),
    };
    detail.can_discharge = true;
    detail.blocking_order_count = 0;

    installPhase15Server({ encounterDetail: () => detail });
    renderApp(DETAIL_PATH);

    const outstanding = await screen.findByRole("table", {
      name: /Outstanding investigations/,
    });
    expect(within(outstanding).getByText("Asha Menon")).toBeInTheDocument();
    expect(screen.getByText("Ready to discharge")).toBeInTheDocument();
  });

  it("reports the gate as blocked and links to it", async () => {
    installPhase15Server();
    renderApp(DETAIL_PATH);

    expect(
      await screen.findByText(/Discharge blocked — 1 investigation has no one/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open discharge gate" }),
    ).toHaveAttribute("href", `/encounters/${ENCOUNTER_ID}/discharge`);
  });

  it("says the server re-checks, so the screen is never the authority", async () => {
    installPhase15Server();
    renderApp(DETAIL_PATH);
    expect(
      await screen.findByText(/re-checks this on the server/),
    ).toBeInTheDocument();
  });

  it("notes when the gate does not apply", async () => {
    installPhase15Server({
      encounterDetail: () =>
        encounterFullDetail({ type: "opd", gate_applies: false, can_discharge: true }),
    });
    renderApp(DETAIL_PATH);
    expect(
      await screen.findByText(/does not apply to this encounter/),
    ).toBeInTheDocument();
  });

  it("handles an encounter with no orders or medications", async () => {
    installPhase15Server({
      encounterDetail: () =>
        encounterFullDetail({
          orders: [],
          medications: [],
          can_discharge: true,
          blocking_order_count: 0,
        }),
    });
    renderApp(DETAIL_PATH);

    expect(
      await screen.findByText("No investigations on this encounter"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No discharge medications recorded"),
    ).toBeInTheDocument();
  });

  it("reports a 404 rather than an empty encounter", async () => {
    installPhase15Server({
      encounterDetail: () => problem(404, "Encounter not found"),
    });
    renderApp(DETAIL_PATH);
    expect(await screen.findByText("Encounter not found")).toBeInTheDocument();
  });

  it("refuses a malformed encounter id without calling the server", async () => {
    const server = installPhase15Server();
    renderApp("/encounters/not-a-uuid");
    expect(
      await screen.findByText("That is not a valid encounter reference"),
    ).toBeInTheDocument();
    expect(server.calls).toHaveLength(0);
  });
});

describe("manual order creation", () => {
  it("creates an order and reports that it now blocks", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(
      await screen.findByRole("button", { name: "Add investigation" }),
    );
    await user.type(screen.getByLabelText("Test code"), "HBA1C");
    await user.type(screen.getByLabelText("Test name"), "HbA1c");
    await user.selectOptions(screen.getByLabelText("Category"), "lab");
    await user.type(screen.getByLabelText(/Turnaround time/), "24");
    await user.click(screen.getByRole("button", { name: "Add investigation" }));

    await screen.findByText(/HbA1c added and outstanding/);

    const body = server.postsTo("/orders")[0].body as Record<string, string>;
    expect(body.test_code).toBe("HBA1C");
    expect(body.test_name).toBe("HbA1c");
    expect(body.category).toBe("lab");
    expect(body.expected_tat_hours).toBe("24");
  });

  it("warns before the fact that a new order blocks discharge", async () => {
    const user = userEvent.setup();
    installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(
      await screen.findByRole("button", { name: "Add investigation" }),
    );
    expect(screen.getByText("This will block discharge")).toBeInTheDocument();
  });

  it("requires a test code and name, and posts nothing without them", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(
      await screen.findByRole("button", { name: "Add investigation" }),
    );
    await user.click(screen.getByRole("button", { name: "Add investigation" }));

    expect(await screen.findAllByText("Required.")).toHaveLength(2);
    expect(server.postsTo("/orders")).toHaveLength(0);
  });

  it("rejects a non-positive turnaround time at the field", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(
      await screen.findByRole("button", { name: "Add investigation" }),
    );
    await user.type(screen.getByLabelText("Test code"), "X");
    await user.type(screen.getByLabelText("Test name"), "Y");
    await user.type(screen.getByLabelText(/Turnaround time/), "0");
    await user.click(screen.getByRole("button", { name: "Add investigation" }));

    expect(
      await screen.findByText("Must be greater than 0."),
    ).toBeInTheDocument();
    expect(server.postsTo("/orders")).toHaveLength(0);
  });

  it("offers no terminal status — a result is recorded by the lab", async () => {
    const user = userEvent.setup();
    installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(
      await screen.findByRole("button", { name: "Add investigation" }),
    );
    const options = within(screen.getByLabelText("Status"))
      .getAllByRole("option")
      .map((o) => o.textContent);

    // An order born `final` never blocks anything: offering it here would be
    // a gate bypass hiding in a dropdown.
    expect(options).toEqual(["ordered", "collected", "in lab"]);
  });

  it("surfaces a server refusal instead of pretending it worked", async () => {
    const user = userEvent.setup();
    installPhase15Server({
      createOrder: () =>
        problem(409, "Encounter is 'discharged' and no longer accepts new orders."),
    });
    renderApp(DETAIL_PATH);

    await user.click(
      await screen.findByRole("button", { name: "Add investigation" }),
    );
    await user.type(screen.getByLabelText("Test code"), "X");
    await user.type(screen.getByLabelText("Test name"), "Y");
    await user.click(screen.getByRole("button", { name: "Add investigation" }));

    expect(
      await screen.findByText(/no longer accepts new orders/),
    ).toBeInTheDocument();
  });

  it("hides the add button once the encounter is closed, and says why", async () => {
    installPhase15Server({
      encounterDetail: () =>
        encounterFullDetail({
          status: "discharged",
          discharged_at: new Date().toISOString(),
          can_add_orders: false,
        }),
    });
    renderApp(DETAIL_PATH);

    // Said twice on purpose: once in the status banner, once next to the
    // orders list where the missing button would otherwise be.
    expect(await screen.findAllByText(/This encounter is discharged/)).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "Add investigation" }),
    ).toBeNull();
    expect(
      screen.getByText(/could not be tracked by the gate/),
    ).toBeInTheDocument();
  });

  it("is operable by keyboard alone", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    const toggle = await screen.findByRole("button", { name: "Add investigation" });
    toggle.focus();
    await user.keyboard("{Enter}");

    await user.click(screen.getByLabelText("Test code"));
    await user.keyboard("CBC");
    await user.click(screen.getByLabelText("Test name"));
    await user.keyboard("Complete Blood Count");
    // Enter inside a text field submits the form, as any form should.
    await user.keyboard("{Enter}");

    await waitFor(() => expect(server.postsTo("/orders")).toHaveLength(1));
  });
});

describe("discharge medication entry", () => {
  it("records a medication", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(await screen.findByRole("button", { name: "Add medication" }));
    await user.type(screen.getByLabelText("Drug name"), "Amoxicillin");
    await user.type(screen.getByLabelText("Dose"), "500 mg");
    await user.type(screen.getByLabelText("Frequency"), "TDS");
    await user.type(screen.getByLabelText("Duration (days)"), "5");
    await user.click(screen.getByLabelText(/This is an antibiotic/));
    await user.click(screen.getByRole("button", { name: "Add medication" }));

    await waitFor(() =>
      expect(server.postsTo("/discharge-medications")).toHaveLength(1),
    );
    const body = server.postsTo("/discharge-medications")[0].body as Record<
      string,
      unknown
    >;
    expect(body.drug_name).toBe("Amoxicillin");
    expect(body.duration_days).toBe("5");
    expect(body.is_antibiotic).toBe(true);
  });

  it("requires only the drug name", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(await screen.findByRole("button", { name: "Add medication" }));
    await user.type(screen.getByLabelText("Drug name"), "Paracetamol");
    await user.click(screen.getByRole("button", { name: "Add medication" }));

    await waitFor(() =>
      expect(server.postsTo("/discharge-medications")).toHaveLength(1),
    );
    const body = server.postsTo("/discharge-medications")[0].body as Record<
      string,
      unknown
    >;
    expect(body.dose).toBeNull();
    expect(body.is_antibiotic).toBe(false);
  });

  it("rejects a non-positive duration at the field", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(await screen.findByRole("button", { name: "Add medication" }));
    await user.type(screen.getByLabelText("Drug name"), "Amoxicillin");
    await user.type(screen.getByLabelText("Duration (days)"), "-3");
    await user.click(screen.getByRole("button", { name: "Add medication" }));

    expect(await screen.findByText("Must be greater than 0.")).toBeInTheDocument();
    expect(server.postsTo("/discharge-medications")).toHaveLength(0);
  });

  it("requires a drug name", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp(DETAIL_PATH);

    await user.click(await screen.findByRole("button", { name: "Add medication" }));
    await user.click(screen.getByRole("button", { name: "Add medication" }));

    expect(await screen.findByText("Required.")).toBeInTheDocument();
    expect(server.postsTo("/discharge-medications")).toHaveLength(0);
  });

  it("surfaces a duplicate refusal from the server", async () => {
    const user = userEvent.setup();
    installPhase15Server({
      addMedication: () =>
        problem(409, "'Amoxicillin' is already recorded on this encounter."),
    });
    renderApp(DETAIL_PATH);

    await user.click(await screen.findByRole("button", { name: "Add medication" }));
    await user.type(screen.getByLabelText("Drug name"), "Amoxicillin");
    await user.click(screen.getByRole("button", { name: "Add medication" }));

    expect(await screen.findByText(/already recorded/)).toBeInTheDocument();
  });

  it("stays available after discharge — Rule B still needs the list", async () => {
    installPhase15Server({
      encounterDetail: () =>
        encounterFullDetail({ status: "discharged", can_add_orders: false }),
    });
    renderApp(DETAIL_PATH);

    expect(
      await screen.findByRole("button", { name: "Add medication" }),
    ).toBeInTheDocument();
  });

  it("lists recorded medications and flags antibiotics", async () => {
    installPhase15Server({
      encounterDetail: () =>
        encounterFullDetail({
          medications: [
            {
              id: "0bbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
              encounter_id: ENCOUNTER_ID,
              drug_name: "Amoxicillin",
              drug_code: null,
              atc_code: "J01CA04",
              dose: "500 mg",
              route: "oral",
              frequency: "TDS",
              duration_days: "5.0",
              is_antibiotic: true,
            },
          ],
        }),
    });
    renderApp(DETAIL_PATH);

    expect(await screen.findByText("Amoxicillin")).toBeInTheDocument();
    expect(screen.getByText("antibiotic")).toBeInTheDocument();
    expect(screen.getByText(/500 mg · oral · TDS · 5.0 days/)).toBeInTheDocument();
  });
});
