/**
 * Phase 3.7 — the lab tech's screen.
 *
 * The tests that matter are about what the screen *refuses to claim*:
 *
 * * the preview says "would", and the screen never treats it as the decision;
 * * nothing is saved by previewing;
 * * a report with no content cannot be saved at all;
 * * the sensitivity grid cannot record an organism as both S and R.
 *
 * The whole route table is mounted, so "Enter result" pointing at a route
 * that does not exist would fail here rather than in a browser.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp } from "../test/render15";
import {
  ENCOUNTER_ID,
  ORDER_A,
  installPhase15Server,
  preview,
} from "../test/server";

afterEach(() => {
  vi.unstubAllGlobals();
});

const PATH = `/encounters/${ENCOUNTER_ID}/orders/${ORDER_A}/result`;

describe("result entry", () => {
  it("is reachable from the encounter's order list", async () => {
    installPhase15Server();
    const user = userEvent.setup();
    renderApp(`/encounters/${ENCOUNTER_ID}`);

    const link = await screen.findByRole("link", {
      name: /enter result for urine culture/i,
    });
    await user.click(link);

    expect(
      await screen.findByRole("heading", { name: /enter a result — urine culture/i }),
    ).toBeInTheDocument();
  });

  it("offers an amendment path on an already-resulted order", async () => {
    installPhase15Server();
    renderApp(`/encounters/${ENCOUNTER_ID}`);

    // An amended report arrives after the original closed the case. That is
    // exactly the report that must not be turned away.
    expect(
      await screen.findByRole("link", {
        name: /enter an amendment for chest x-ray/i,
      }),
    ).toBeInTheDocument();
  });

  it("cannot be saved while the report is empty", async () => {
    installPhase15Server();
    renderApp(PATH);

    const save = await screen.findByRole("button", { name: /save result/i });
    expect(save).toBeDisabled();
    expect(
      screen.getByText(/a report needs a value, an organism or a section/i),
    ).toBeInTheDocument();
  });

  it("asks for nothing until there is something to grade", async () => {
    const server = installPhase15Server();
    renderApp(PATH);

    await screen.findByRole("heading", { name: /enter a result/i });
    // An empty form graded as "no classifiable content" would read as a
    // verdict on a report nobody has typed yet.
    expect(server.postsTo("/results/preview")).toHaveLength(0);
    expect(
      screen.getByText(/enter a value, an organism or a report section/i),
    ).toBeInTheDocument();
  });

  it("previews a numeric value and shows the predicted severity", async () => {
    const server = installPhase15Server({
      resultPreview: () =>
        preview({
          severity: "critical",
          would_auto_close: false,
          rules: [
            {
              rule_id: "A_numeric",
              severity: "critical",
              reason_code: "NUM_ABOVE_CRITICAL_HIGH",
              subject: "Potassium",
              offending_drug: null,
              alternatives_available: [],
            },
          ],
        }),
    });
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add a test/i }));
    await user.type(screen.getByLabelText(/test name, row 1/i), "Potassium");
    await user.type(screen.getByLabelText(/^value, row 1$/i), "7.4");

    expect(await screen.findByText(/^critical$/i)).toBeInTheDocument();
    expect(
      screen.getByText(/at or above the critical high threshold/i),
    ).toBeInTheDocument();

    // The last call, not the first: the panel re-grades on every keystroke,
    // so the earlier ones carry "7" and "7.".
    const last = server.postsTo("/results/preview").at(-1);
    expect(last?.body).toMatchObject({
      analytes: [{ test_name: "Potassium", value_numeric: "7.4" }],
    });
  });

  it("sends a censored value as text rather than dropping it", async () => {
    const server = installPhase15Server();
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add a test/i }));
    await user.type(screen.getByLabelText(/test name, row 1/i), "Troponin");
    await user.type(screen.getByLabelText(/^value, row 1$/i), "<0.01");

    // ">1000" on a troponin is not a missing value, it is the worst one.
    await waitFor(() => expect(server.postsTo("/results/preview")).not.toHaveLength(0));
    const last = server.postsTo("/results/preview").at(-1);
    expect(last?.body).toMatchObject({
      analytes: [{ test_name: "Troponin", value_raw: "<0.01", value_numeric: null }],
    });
  });

  it("grades a culture against the discharge antibiotics", async () => {
    const server = installPhase15Server({
      resultPreview: () =>
        preview({
          severity: "critical",
          would_auto_close: false,
          discharge_antibiotics: ["Monocef"],
          rules: [
            {
              rule_id: "B_culture",
              severity: "critical",
              reason_code: "CULT_RESISTANT_TO_DISCHARGE_DRUG",
              subject: "Escherichia coli",
              offending_drug: "Monocef",
              alternatives_available: ["nitrofurantoin"],
            },
          ],
        }),
    });
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add an organism/i }));
    await user.type(screen.getByLabelText(/^organism$/i), "Escherichia coli");
    await user.type(screen.getByLabelText(/antibiotic 1 for organism 1/i), "Ceftriaxone");
    await user.click(screen.getByRole("radio", { name: /^R for Ceftriaxone$/i }));

    const panel = await screen.findByRole("complementary", {
      name: /predicted severity/i,
    });
    expect(await within(panel).findByText(/^critical$/i)).toBeInTheDocument();
    // The tech is told what the patient is on and what to switch to. Monocef
    // appears twice on purpose: once as the offending drug on the rule line,
    // once in the list of what the culture was compared against.
    expect(within(panel).getAllByText(/monocef/i).length).toBeGreaterThan(0);
    expect(
      within(panel).getByText(/susceptible on this panel: nitrofurantoin/i),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText(/resistant to a discharge antibiotic/i),
    ).toBeInTheDocument();

    const last = server.postsTo("/results/preview").at(-1);
    expect(last?.body).toMatchObject({
      organisms: [
        {
          organism_name: "Escherichia coli",
          sensitivities: [{ antibiotic_name: "Ceftriaxone", interpretation: "R" }],
        },
      ],
    });
  });

  it("cannot record one antibiotic as both susceptible and resistant", async () => {
    installPhase15Server();
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add an organism/i }));
    await user.type(screen.getByLabelText(/^organism$/i), "Escherichia coli");
    await user.type(screen.getByLabelText(/antibiotic 1 for organism 1/i), "Ceftriaxone");

    const resistant = screen.getByRole("radio", { name: /^R for Ceftriaxone$/i });
    const susceptible = screen.getByRole("radio", { name: /^S for Ceftriaxone$/i });
    await user.click(resistant);

    // Radios, not checkboxes: S, I and R are exclusive.
    expect(resistant).toBeChecked();
    expect(susceptible).not.toBeChecked();
  });

  it("says a narrative would keep the case open", async () => {
    installPhase15Server({
      resultPreview: () =>
        preview({
          severity: "normal",
          would_auto_close: false,
          rules: [
            {
              rule_id: "C_narrative",
              severity: "normal",
              reason_code: "NARR_ALL_HITS_NEGATED",
              subject: "impression",
              offending_drug: null,
              alternatives_available: [],
            },
          ],
        }),
    });
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add a section/i }));
    await user.type(screen.getByLabelText(/^text$/i), "No evidence of malignancy.");

    const panel = await screen.findByRole("complementary", {
      name: /predicted severity/i,
    });
    await waitFor(() =>
      expect(within(panel).getByText(/would close the case:/i)).toBeInTheDocument(),
    );
    expect(within(panel).getByText(/^no$/i)).toBeInTheDocument();
  });

  it("never calls the preview a decision", async () => {
    installPhase15Server();
    renderApp(PATH);

    const panel = await screen.findByRole("complementary", {
      name: /predicted severity/i,
    });
    expect(
      within(panel).getByText(/nothing is saved until you press save/i),
    ).toBeInTheDocument();
  });

  it("saves the report and says the engine decides the severity", async () => {
    const server = installPhase15Server();
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add a test/i }));
    await user.type(screen.getByLabelText(/test name, row 1/i), "Potassium");
    await user.type(screen.getByLabelText(/^value, row 1$/i), "7.4");
    await user.type(screen.getByLabelText(/lab reference/i), "ACC-9");
    await user.click(screen.getByRole("button", { name: /save result/i }));

    expect(await screen.findByText(/result recorded/i)).toBeInTheDocument();
    // The screen does not claim a severity it did not decide.
    expect(
      screen.getByText(/the severity it decides is the one of record/i),
    ).toBeInTheDocument();

    const [saved] = server.postsTo("/results");
    expect(saved.body).toMatchObject({
      report_status: "final",
      source_ref: "ACC-9",
      analytes: [{ test_name: "Potassium", value_numeric: "7.4" }],
    });
  });

  it("drops half-typed rows rather than sending them", async () => {
    const server = installPhase15Server();
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add a test/i }));
    await user.type(screen.getByLabelText(/test name, row 1/i), "Potassium");
    await user.type(screen.getByLabelText(/^value, row 1$/i), "4.2");
    // A second row with a name and no value is not a finding.
    await user.click(screen.getByRole("button", { name: /add a test/i }));
    await user.type(screen.getByLabelText(/test name, row 2/i), "Sodium");
    await user.click(screen.getByRole("button", { name: /save result/i }));

    await screen.findByText(/result recorded/i);
    const [saved] = server.postsTo("/results");
    expect(saved.body).toMatchObject({ analytes: [{ test_name: "Potassium" }] });
  });

  it("shows the server's refusal rather than a status code", async () => {
    installPhase15Server({
      recordResult: () => ({
        __problem: true,
        title: "A manual result with reference 'ACC-9' is already recorded.",
        status: 409,
      }),
    });
    const user = userEvent.setup();
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: /add a test/i }));
    await user.type(screen.getByLabelText(/test name, row 1/i), "Potassium");
    await user.type(screen.getByLabelText(/^value, row 1$/i), "4.2");
    await user.click(screen.getByRole("button", { name: /save result/i }));

    expect(await screen.findByText(/already recorded/i)).toBeInTheDocument();
    expect(screen.queryByText(/result recorded\./i)).not.toBeInTheDocument();
  });

  it("explains what a preliminary report means before it is saved", async () => {
    installPhase15Server();
    const user = userEvent.setup();
    renderApp(PATH);

    await user.selectOptions(
      await screen.findByLabelText(/report status/i),
      "preliminary",
    );
    expect(
      screen.getByText(/held, not closed — the final one is still owed/i),
    ).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/report status/i), "amended");
    expect(
      screen.getByText(/reopens a closed case and re-notifies/i),
    ).toBeInTheDocument();
  });

  it("is operable from the keyboard alone", async () => {
    installPhase15Server();
    const user = userEvent.setup();
    renderApp(PATH);

    const add = await screen.findByRole("button", { name: /add a test/i });
    add.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByLabelText(/test name, row 1/i)).toBeInTheDocument();

    await user.keyboard("{Tab}");
    await user.keyboard("Potassium");
    expect(screen.getByLabelText(/test name, row 1/i)).toHaveValue("Potassium");
  });

  it("refuses an order that is not on this encounter", async () => {
    installPhase15Server();
    renderApp(
      `/encounters/${ENCOUNTER_ID}/orders/99999999-9999-4999-8999-999999999999/result`,
    );
    expect(
      await screen.findByText(/not on this encounter/i),
    ).toBeInTheDocument();
  });
});
