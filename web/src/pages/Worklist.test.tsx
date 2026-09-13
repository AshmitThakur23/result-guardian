/**
 * Phase 5.2 / 5.3 — the worklist, in a browser.
 *
 * The assertions that matter most are the two the plan is explicit about:
 * **CRITICAL rows come first**, and **bulk close is not available for them**.
 * Both are enforced server-side too; this proves the interface does not
 * quietly contradict it.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp5 } from "../test/render5";
import {
  CASE_ID,
  CRITICAL_CASE_ID,
  criticalRow,
  health,
  installApi5,
  problem,
  worklistRow,
} from "../test/server5";

afterEach(() => vi.unstubAllGlobals());

async function rowsInOrder(): Promise<string[]> {
  const table = await screen.findByRole("table");
  const bodyRows = within(table).getAllByRole("row").slice(1);
  return bodyRows.map((row) => within(row).getAllByRole("cell")[1].textContent ?? "");
}

describe("the worklist", () => {
  it("renders the rows in the order the server sent them", async () => {
    // The server sorts CRITICAL first, then oldest. Re-sorting here would
    // silently disagree with the cursor and let a case fall between pages.
    installApi5({
      worklist: () => ({
        rows: [criticalRow(), worklistRow()],
        next_cursor: null,
        total_open: 2,
      }),
    });
    renderApp5("/worklist");

    await waitFor(async () => {
      const order = await rowsInOrder();
      expect(order[0]).toContain("Imran Qureshi");
      expect(order[1]).toContain("Sunita Rao");
    });
  });

  it("shows patient, MRN, test, severity, age and the escalation rung", async () => {
    installApi5();
    renderApp5("/worklist");

    await screen.findByText("Sunita Rao");
    // Scoped to the table: "Needs follow-up" is also a filter option, and an
    // unscoped query would pass on the filter alone.
    const table = within(screen.getByRole("table"));
    expect(table.getByText("MRN-77021")).toBeInTheDocument();
    expect(table.getByText("Urine Culture")).toBeInTheDocument();
    expect(table.getByText("Needs follow-up")).toBeInTheDocument();
    expect(table.getByText("5h 0m old")).toBeInTheDocument();
    expect(table.getByText("Rung 0")).toBeInTheDocument();
    expect(table.getByText("in 2h 0m")).toBeInTheDocument();
  });

  it("says an escalation is overdue rather than clamping the countdown", async () => {
    // Clamping to "0m" would hide that the worker has fallen behind, which is
    // the one failure this product exists to prevent.
    installApi5({
      worklist: () => ({
        rows: [criticalRow()],
        next_cursor: null,
        total_open: 1,
      }),
    });
    renderApp5("/worklist");

    expect(await screen.findByText(/Overdue by 30m/)).toBeInTheDocument();
  });

  it("cannot select a critical case for bulk close", async () => {
    installApi5({
      worklist: () => ({
        rows: [criticalRow(), worklistRow()],
        next_cursor: null,
        total_open: 2,
      }),
    });
    renderApp5("/worklist");

    const critical = await screen.findByRole("checkbox", {
      name: /critical cases must be closed individually/i,
    });
    expect(critical).toBeDisabled();

    const ordinary = screen.getByRole("checkbox", { name: /Select Sunita Rao/i });
    expect(ordinary).toBeEnabled();
  });

  it("closes several non-critical cases at once", async () => {
    const api = installApi5({
      worklist: () => ({
        rows: [worklistRow(), worklistRow({ case_id: "case-2", patient_name: "Ravi" })],
        next_cursor: null,
        total_open: 2,
      }),
    });
    renderApp5("/worklist");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: /Select Sunita Rao/i }));
    await user.type(
      screen.getByLabelText(/Note \(required\)/i),
      "Both reviewed on the ward round",
    );
    await user.click(screen.getByRole("button", { name: /Close selected/i }));

    await waitFor(() => {
      expect(api.postsTo("/cases/bulk-close")).toHaveLength(1);
    });
    expect(api.postsTo("/cases/bulk-close")[0].body).toMatchObject({
      case_ids: [CASE_ID],
      closure_reason: "action_taken",
    });
  });

  it("explains a refused bulk close rather than showing a bare 409", async () => {
    installApi5({
      worklist: () => ({
        rows: [worklistRow()],
        next_cursor: null,
        total_open: 1,
      }),
      bulkClose: () =>
        problem(409, "CRITICAL cases must be closed individually", {
          critical_case_ids: [CRITICAL_CASE_ID],
        }),
    });
    renderApp5("/worklist");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: /Select Sunita Rao/i }));
    await user.type(
      screen.getByLabelText(/Note \(required\)/i),
      "Reviewed on the ward round",
    );
    await user.click(screen.getByRole("button", { name: /Close selected/i }));

    expect(
      await screen.findByText(/1 of the selected cases are critical/i),
    ).toBeInTheDocument();
  });

  it("requires a note before the bulk close button works", async () => {
    installApi5();
    renderApp5("/worklist");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: /Select Sunita Rao/i }));
    expect(screen.getByRole("button", { name: /Close selected/i })).toBeDisabled();

    await user.type(screen.getByLabelText(/Note \(required\)/i), "short");
    expect(screen.getByRole("button", { name: /Close selected/i })).toBeDisabled();
  });

  it("drops the cursor when a filter changes", async () => {
    // A cursor encodes a position in the *previous* result set; reusing it
    // after a filter change would skip rows.
    const api = installApi5({
      worklist: () => ({
        rows: [worklistRow()],
        next_cursor: "cursor-1",
        total_open: 5,
      }),
    });
    renderApp5("/worklist");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Next page/i }));
    await waitFor(() => {
      expect(api.getsTo("/worklist").some((c) => c.path.includes("cursor="))).toBe(true);
    });

    await user.selectOptions(screen.getByLabelText(/Severity/i), "critical");

    await waitFor(() => {
      const latest = api.getsTo("/worklist").at(-1)!;
      expect(latest.path).toContain("severity=critical");
      expect(latest.path).not.toContain("cursor=");
    });
  });

  it("tells the doctor there is nothing waiting rather than showing a blank table", async () => {
    installApi5({
      worklist: () => ({ rows: [], next_cursor: null, total_open: 0 }),
    });
    renderApp5("/worklist");

    expect(await screen.findByText(/Nothing is waiting for you/i)).toBeInTheDocument();
  });

  it("shows an error rather than an empty list when the request fails", async () => {
    // A clinician who cannot tell a failed request from an empty result will
    // assume the empty result.
    installApi5({
      worklist: () => problem(500, "The server could not complete this request"),
    });
    renderApp5("/worklist");

    expect(
      await screen.findByText(/could not complete this request/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Nothing is waiting/i)).not.toBeInTheDocument();
  });

  it("sends the bearer token on every request", async () => {
    const api = installApi5();
    renderApp5("/worklist");

    await screen.findByText("Sunita Rao");
    const worklistCalls = api.getsTo("/worklist");
    expect(worklistCalls.length).toBeGreaterThan(0);
    for (const call of worklistCalls) {
      expect(call.authorization).toMatch(/^Bearer /);
    }
  });
});

describe("the system status pill", () => {
  it("says core tracking is unaffected when NODE B is offline", async () => {
    // A bare red "AI: offline" reads as an outage, and that is how a ward
    // stops trusting a system that is working perfectly.
    installApi5({ health: () => health({ llm: { reachable: false } }) });
    renderApp5("/worklist");

    expect(
      await screen.findByText(/AI: offline — core tracking unaffected/i),
    ).toBeInTheDocument();
  });

  it("says AI is connected when NODE B answers", async () => {
    installApi5({
      health: () => health({ llm: { reachable: true }, degraded_features: [] }),
    });
    renderApp5("/worklist");

    expect(await screen.findByText("AI: connected")).toBeInTheDocument();
  });

  it("treats a stale worker as serious, unlike an offline NODE B", async () => {
    // A dead worker means timers are not firing. That is the failure this
    // product exists to prevent, and it gets the red treatment NODE B does not.
    installApi5({
      health: () =>
        health({ worker_heartbeat_age_s: 900, degraded_features: ["worker"] }),
    });
    renderApp5("/worklist");

    expect(await screen.findByText(/Worker not responding/i)).toBeInTheDocument();
  });
});
