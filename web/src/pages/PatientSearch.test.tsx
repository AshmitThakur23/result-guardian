/**
 * Phase 1.5 — patient search screen.
 *
 * The states matter more than the happy path here. A clinician who cannot
 * tell "no such patient" from "the request failed" will assume the first and
 * go and create a duplicate record.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp } from "../test/render15";
import { PATIENT_ID, installPhase15Server, patientRow, problem } from "../test/server";

afterEach(() => vi.unstubAllGlobals());

describe("patient search", () => {
  it("prompts before anything is typed, and asks the server nothing", async () => {
    const server = installPhase15Server();
    renderApp("/patients");

    expect(await screen.findByText("Type to search")).toBeInTheDocument();
    // The endpoint refuses an empty q deliberately. Asking anyway would only
    // render a 422 at someone who has not typed yet.
    expect(server.calls).toHaveLength(0);
  });

  it("searches and lists matches", async () => {
    const user = userEvent.setup();
    installPhase15Server();
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "Sunita");

    expect(await screen.findByText("Sunita Rao")).toBeInTheDocument();
    expect(screen.getByText("MRN-77021")).toBeInTheDocument();
  });

  it("debounces rather than firing per keystroke", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "Sunita");
    await screen.findByText("Sunita Rao");

    // Six characters typed, nowhere near six requests.
    const searches = server.calls.filter((c) => c.path.startsWith("/patients?"));
    expect(searches.length).toBeLessThan(3);
  });

  it("escapes the query rather than building a URL by hand", async () => {
    const user = userEvent.setup();
    const server = installPhase15Server();
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "a&b=c");
    await waitFor(() =>
      expect(
        server.calls.some((c) => c.path.includes("a%26b%3Dc")),
      ).toBe(true),
    );
  });

  it("shows the open-encounter count so the clerk can pick the admission", async () => {
    const user = userEvent.setup();
    installPhase15Server();
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "Sunita");
    expect(await screen.findByText("1 active")).toBeInTheDocument();
  });

  it("says plainly when nothing matched", async () => {
    const user = userEvent.setup();
    installPhase15Server({ patientSearch: () => [] });
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "Zzzz");
    expect(await screen.findByText(/No patient matches/)).toBeInTheDocument();
  });

  it("distinguishes a failed request from an empty result", async () => {
    const user = userEvent.setup();
    installPhase15Server({
      patientSearch: () => problem(500, "The server could not complete this request"),
    });
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "Sunita");

    expect(
      await screen.findByText("The server could not complete this request"),
    ).toBeInTheDocument();
    // The critical part: it must NOT read as "no such patient".
    expect(screen.queryByText(/No patient matches/)).toBeNull();
  });

  it("offers a retry after a failure", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    installPhase15Server({
      patientSearch: () => {
        attempt += 1;
        return attempt === 1 ? problem(503, "Service unavailable") : [patientRow()];
      },
    });
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "Sunita");
    await user.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Sunita Rao")).toBeInTheDocument();
  });

  it("navigates to the patient by keyboard alone", async () => {
    const user = userEvent.setup();
    installPhase15Server();
    renderApp("/patients");

    await user.type(screen.getByLabelText(/Search by MRN/), "Sunita");
    const link = await screen.findByRole("link", { name: /Sunita Rao/ });
    expect(link).toHaveAttribute("href", `/patients/${PATIENT_ID}`);

    link.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("ENC-2026-0042")).toBeInTheDocument();
  });

  it("sends the user to search from the root path", async () => {
    installPhase15Server();
    renderApp("/");
    expect(
      await screen.findByRole("heading", { name: "Find a patient" }),
    ).toBeInTheDocument();
  });

  it("shows a not-found page for an unknown route", async () => {
    installPhase15Server();
    renderApp("/nowhere");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
  });
});

describe("patient detail", () => {
  it("lists encounters and links to each", async () => {
    installPhase15Server();
    renderApp(`/patients/${PATIENT_ID}`);

    expect(
      await screen.findByRole("heading", { name: "Sunita Rao" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Active (1)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /ENC-2026-0042/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/encounters/"),
    );
  });

  it("refuses a malformed patient id without calling the server", async () => {
    const server = installPhase15Server();
    renderApp("/patients/not-a-uuid");

    expect(
      await screen.findByText("That is not a valid patient reference"),
    ).toBeInTheDocument();
    expect(server.calls).toHaveLength(0);
  });

  it("reports a 404 rather than rendering an empty patient", async () => {
    installPhase15Server({ patient: () => problem(404, "Patient not found") });
    renderApp(`/patients/${PATIENT_ID}`);

    expect(await screen.findByText("Patient not found")).toBeInTheDocument();
  });

  it("handles a patient with no encounters", async () => {
    installPhase15Server({
      patient: () => ({ patient: patientRow({ active_encounter_count: 0 }), encounters: [] }),
    });
    renderApp(`/patients/${PATIENT_ID}`);

    expect(
      await screen.findByText("No encounters recorded for this patient"),
    ).toBeInTheDocument();
  });
});
