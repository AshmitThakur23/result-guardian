/**
 * Phase 5.2 / 5.3 — the case page and the closure workflow.
 *
 * The clinical assertions here are the ones a wrong answer would matter for:
 * that the plain-language explanation is shown, that an abnormal value is
 * marked *with a word and not only a colour*, and that a closure cannot be
 * recorded without saying why.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp5 } from "../test/render5";
import { CASE_ID, caseDetail, installApi5, problem } from "../test/server5";

afterEach(() => vi.unstubAllGlobals());

const GOOD_NOTE = "Patient recalled and antibiotic changed to nitrofurantoin";

describe("the case page", () => {
  it("leads with the plain-language explanation", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);

    expect(
      await screen.findByText(
        "Amoxicillin-clavulanate (discharge medication) is Resistant for E. coli.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/prescription needs review/i)).toBeInTheDocument();
  });

  it("keeps the reason code visible next to the sentence", async () => {
    // A clinician who wants to know exactly which rule fired should not have
    // to open the audit trail to find out.
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);

    expect(
      await screen.findByText(/CULT_RESISTANT_TO_DISCHARGE_DRUG/),
    ).toBeInTheDocument();
  });

  it("renders analytes as a table with the reference range", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);

    await screen.findByText("Potassium");
    expect(screen.getByText("3.5 – 5.1")).toBeInTheDocument();
    expect(screen.getByText("135 – 145")).toBeInTheDocument();
  });

  it("marks an abnormal value with a word, not only a colour", async () => {
    // Colour alone is the same chip to a colour-blind reader and to a printed
    // report.
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);

    await screen.findByText("Potassium");
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("Normal")).toBeInTheDocument();
  });

  it("renders sensitivities as a grid, spelled out", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);

    expect(await screen.findByText("Amoxicillin-clavulanate")).toBeInTheDocument();
    // "Resistant", not "R": the single letter is lab shorthand.
    expect(screen.getByText("Resistant")).toBeInTheDocument();
    expect(screen.getByText("Sensitive")).toBeInTheDocument();
  });

  it("renders the narrative as text", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);

    expect(
      await screen.findByText("Significant growth of E. coli."),
    ).toBeInTheDocument();
  });

  it("shows the case_events timeline", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);

    expect(await screen.findByText("Case opened")).toBeInTheDocument();
    expect(screen.getByText("Classified by the rule engine")).toBeInTheDocument();
  });

  it("opens without a result rather than failing", async () => {
    // An awaiting-result case is the most important one to be able to open.
    installApi5({
      caseDetail: () =>
        caseDetail({
          result: null,
          analytes: [],
          organisms: [],
          narratives: [],
          explanations: [],
        }),
    });
    renderApp5(`/cases/${CASE_ID}`);

    expect(await screen.findByText(/No result has arrived yet/i)).toBeInTheDocument();
    expect(screen.getByText(/escalates until one does/i)).toBeInTheDocument();
  });
});

describe("closing a case", () => {
  it("asks the question that belongs to the chosen reason", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Acknowledge and close/i }));
    expect(screen.getByText("What was done, and when?")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText(/Closure reason/i),
      "not_clinically_relevant",
    );
    expect(
      screen.getByText("Why is this not clinically relevant for this patient?"),
    ).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText(/Closure reason/i),
      "patient_uncontactable",
    );
    expect(screen.getByText("What contact attempts were made?")).toBeInTheDocument();
  });

  it("will not submit without a note", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Acknowledge and close/i }));
    expect(screen.getByRole("button", { name: /Close this case/i })).toBeDisabled();

    await user.type(screen.getByLabelText(/What was done/i), "done");
    expect(screen.getByRole("button", { name: /Close this case/i })).toBeDisabled();

    await user.clear(screen.getByLabelText(/What was done/i));
    await user.type(screen.getByLabelText(/What was done/i), GOOD_NOTE);
    expect(screen.getByRole("button", { name: /Close this case/i })).toBeEnabled();
  });

  it("sends the reason and the note, and never an actor", async () => {
    // The closer is the authenticated user. A client-supplied actor would let
    // anyone sign a closure in a colleague's name.
    const api = installApi5();
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Acknowledge and close/i }));
    await user.type(screen.getByLabelText(/What was done/i), GOOD_NOTE);
    await user.click(screen.getByRole("button", { name: /Close this case/i }));

    await waitFor(() => expect(api.postsTo("/close")).toHaveLength(1));
    const body = api.postsTo("/close")[0].body as Record<string, unknown>;
    expect(body.closure_reason).toBe("action_taken");
    expect(body.closure_note).toBe(GOOD_NOTE);
    expect(body).not.toHaveProperty("acknowledged_by");
  });

  it("demands the original case when closing as a duplicate", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Acknowledge and close/i }));
    await user.selectOptions(screen.getByLabelText(/Closure reason/i), "duplicate_report");
    await user.type(screen.getByLabelText(/Which case does this duplicate/i), GOOD_NOTE);

    expect(screen.getByRole("button", { name: /Close this case/i })).toBeDisabled();
    await user.type(screen.getByLabelText(/Original case ID/i), "case-original");
    expect(screen.getByRole("button", { name: /Close this case/i })).toBeEnabled();
  });

  it("warns before closing a critical case", async () => {
    installApi5();
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Acknowledge and close/i }));
    expect(screen.getByText(/This is a critical result/i)).toBeInTheDocument();
    expect(screen.getByText(/never in bulk/i)).toBeInTheDocument();
  });

  it("shows the server's refusal rather than a status code", async () => {
    installApi5({
      close: () =>
        problem(422, "Describe the action taken (what was done, and when)."),
    });
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Acknowledge and close/i }));
    await user.type(screen.getByLabelText(/What was done/i), GOOD_NOTE);
    await user.click(screen.getByRole("button", { name: /Close this case/i }));

    expect(await screen.findByText(/Describe the action taken/i)).toBeInTheDocument();
  });
});

describe("a closed case", () => {
  it("offers reopening instead of closing, and says the history is kept", async () => {
    installApi5({
      caseDetail: () =>
        caseDetail({
          state: "closed",
          closed_at: new Date().toISOString(),
          closure_reason: "action_taken",
          closure_note: GOOD_NOTE,
          can_acknowledge: false,
        }),
    });
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    expect(await screen.findByText(/This case is closed/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Acknowledge and close/i }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Reopen$/i }));
    expect(
      screen.getByText(/The original closure stays in the history/i),
    ).toBeInTheDocument();
  });

  it("requires a real reason to reopen", async () => {
    const api = installApi5({
      caseDetail: () =>
        caseDetail({
          state: "closed",
          closed_at: new Date().toISOString(),
          closure_reason: "action_taken",
          can_acknowledge: false,
        }),
    });
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /^Reopen$/i }));
    await user.type(screen.getByLabelText(/Why is this being reopened/i), "oops");
    expect(screen.getByRole("button", { name: /Reopen this case/i })).toBeDisabled();

    await user.type(
      screen.getByLabelText(/Why is this being reopened/i),
      " — amended report arrived",
    );
    await user.click(screen.getByRole("button", { name: /Reopen this case/i }));

    await waitFor(() => expect(api.postsTo("/reopen")).toHaveLength(1));
  });

  it("shows how many times a case has been reopened", async () => {
    installApi5({ caseDetail: () => caseDetail({ reopened_count: 3 }) });
    renderApp5(`/cases/${CASE_ID}`);

    expect(
      await screen.findByText(/This case has been reopened 3 times/i),
    ).toBeInTheDocument();
  });
});

describe("adding a note", () => {
  it("appends to the timeline without changing the case", async () => {
    const api = installApi5();
    renderApp5(`/cases/${CASE_ID}`);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Add a note/i }));
    await user.type(
      screen.getByLabelText(/^Note$/i),
      "Rang the patient, no answer. Will try again this evening.",
    );
    await user.click(screen.getByRole("button", { name: /Add the note/i }));

    await waitFor(() => expect(api.postsTo("/notes")).toHaveLength(1));
    expect(api.postsTo("/close")).toHaveLength(0);
  });
});

describe("a case outside your scope", () => {
  it("reads as not found, not as forbidden", async () => {
    // 403 would confirm the case exists for a patient the caller named.
    installApi5({ caseDetail: () => problem(404, "Case not found") });
    renderApp5(`/cases/${CASE_ID}`);

    expect(await screen.findByText("Case not found")).toBeInTheDocument();
  });
});

describe("the break-glass banner", () => {
  it("stays on screen while elevated access is active", async () => {
    installApi5({
      refresh: () => ({
        access_token: "a",
        refresh_token: "r",
        token_type: "bearer",
        expires_in: 900,
        must_change_password: false,
        user: {
          id: "11111111-1111-4111-8111-111111111111",
          employee_code: "D-1001",
          full_name: "Asha Menon",
          role: "doctor",
          department_id: null,
          must_change_password: false,
          break_glass: true,
        },
      }),
    });
    renderApp5(`/cases/${CASE_ID}`);

    const banner = await screen.findByText(/Break-glass access is active/i);
    expect(within(banner).queryByText("x")).toBeNull();
    expect(banner).toBeInTheDocument();
  });
});
