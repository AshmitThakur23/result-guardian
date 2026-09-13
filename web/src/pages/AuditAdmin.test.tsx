/**
 * Phase 5.4 / 5.5 in the browser — the audit viewer and the kill switch.
 *
 * Two things are worth a test each, because both are how the interface can
 * lie about something serious:
 *
 * * a broken hash chain must read as **tampering**, named and located, not as
 *   a generic error;
 * * the NODE B panel must say that turning inference off costs nothing that
 *   keeps a patient safe, or an administrator will not dare use the switch in
 *   the incident it exists for.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp5 } from "../test/render5";
import { authUser, installApi5, tokens } from "../test/server5";

afterEach(() => vi.unstubAllGlobals());

function asAuditor(handlers: Parameters<typeof installApi5>[0] = {}) {
  return installApi5({
    refresh: () => tokens({ user: authUser({ role: "auditor" }) }),
    ...handlers,
  });
}

function asAdmin(handlers: Parameters<typeof installApi5>[0] = {}) {
  return installApi5({
    refresh: () => tokens({ user: authUser({ role: "admin" }) }),
    ...handlers,
  });
}

describe("the audit viewer", () => {
  it("does not verify the chain until asked", async () => {
    // Recomputing every hash is real work, and a number that refreshes on its
    // own invites nobody to look at it.
    const api = asAuditor();
    renderApp5("/audit");

    await screen.findByRole("heading", { name: /Audit trail/i });
    expect(api.getsTo("/audit/verify")).toHaveLength(0);
  });

  it("reports an intact chain with the head hash", async () => {
    asAuditor();
    renderApp5("/audit");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Verify the chain/i }));
    expect(await screen.findByText(/The audit chain is intact/i)).toBeInTheDocument();
    expect(screen.getByText(/120 rows recomputed and matched/i)).toBeInTheDocument();
  });

  it("names where a broken chain breaks, and says what it means", async () => {
    asAuditor({
      verify: () => ({
        intact: false,
        rows_checked: 4811,
        first_break_seq: 4812,
        first_break_reason:
          "row_hash does not match the row's contents — this row was modified after it was written",
        head_seq: null,
        head_hash: null,
        window_anchored: true,
        verified_at: new Date().toISOString(),
      }),
    });
    renderApp5("/audit");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Verify the chain/i }));

    expect(await screen.findByText(/The audit chain is broken/i)).toBeInTheDocument();
    expect(screen.getByText("4812")).toBeInTheDocument();
    expect(screen.getByText(/this row was modified after it was written/i)).toBeInTheDocument();
    expect(screen.getByText(/Escalate this/i)).toBeInTheDocument();
  });

  it("shows a break-glass reason on the row it belongs to", async () => {
    asAuditor({
      audit: () => ({
        rows: [
          {
            seq: 1,
            occurred_at: new Date().toISOString(),
            actor_user_id: null,
            actor_name: "Asha Menon",
            actor_employee_code: "D-1001",
            actor_ip: "10.0.0.5",
            action: "auth.break_glass",
            entity_type: "user",
            entity_id: "abc",
            before: null,
            after: null,
            prev_hash: "0".repeat(64),
            row_hash: "a".repeat(64),
            break_glass_reason: "Covering Surgery overnight, ward called",
          },
        ],
        next_after_seq: null,
      }),
    });
    renderApp5("/audit");

    expect(
      await screen.findByText(/Break-glass: Covering Surgery overnight/i),
    ).toBeInTheDocument();
  });

  it("keeps a doctor out", async () => {
    installApi5();
    renderApp5("/audit");

    expect(
      await screen.findByText(/You do not have access to this page/i),
    ).toBeInTheDocument();
  });
});

describe("the NODE B panel", () => {
  it("says plainly that turning inference off costs no safety", async () => {
    asAdmin();
    renderApp5("/admin");

    expect(
      await screen.findByText(/Turning this off costs nothing that keeps a patient safe/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Every safety guarantee .* runs on NODE A and is unaffected/i),
    ).toBeInTheDocument();
  });

  it("shows unreachable without implying an outage", async () => {
    asAdmin();
    renderApp5("/admin");

    await screen.findByText("Reachable right now");
    expect(screen.getByText(/No — ConnectError/)).toBeInTheDocument();
  });

  it("requires a reason before the kill switch can be flipped", async () => {
    asAdmin();
    renderApp5("/admin");
    const user = userEvent.setup();

    const button = await screen.findByRole("button", { name: /Turn inference off/i });
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText(/Why\?/i), "NODE B returning nonsense");
    expect(button).toBeEnabled();
  });

  it("sends the reason with the switch", async () => {
    const api = asAdmin();
    renderApp5("/admin");
    const user = userEvent.setup();

    await user.type(
      await screen.findByLabelText(/Why\?/i),
      "NODE B returning nonsense summaries",
    );
    await user.click(screen.getByRole("button", { name: /Turn inference off/i }));

    await waitFor(() =>
      expect(api.postsTo("/admin/node-b/kill-switch")).toHaveLength(1),
    );
    expect(api.postsTo("/admin/node-b/kill-switch")[0].body).toMatchObject({
      llm_enabled: false,
      reason: "NODE B returning nonsense summaries",
    });
  });

  it("says where the setting came from", async () => {
    // "database" means an admin flipped it; "environment" means nobody has.
    asAdmin({
      nodeB: () => ({
        llm_enabled: false,
        llm_enabled_source: "database",
        kill_switch_reason: "Scheduled maintenance on NODE B",
        reachable: false,
        base_url: "http://192.168.1.50:11434",
        model: "qwen3:4b",
        probe_error: null,
        safety_note: "NODE B is an accelerator.",
      }),
    });
    renderApp5("/admin");

    expect(await screen.findByText(/set in the database/i)).toBeInTheDocument();
    expect(screen.getByText("Scheduled maintenance on NODE B")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Turn inference on/i }),
    ).toBeInTheDocument();
  });

  it("keeps a doctor out of the admin screens", async () => {
    installApi5();
    renderApp5("/admin");

    expect(
      await screen.findByText(/You do not have access to this page/i),
    ).toBeInTheDocument();
  });
});
