/**
 * Phase 5.1 in the browser — signing in, staying in, and being kept out.
 *
 * The interesting assertions are about what the *client* does, since the
 * server-side rules are tested against a real PostgreSQL elsewhere:
 *
 * * the access token never reaches `localStorage`;
 * * a 401 rotates the refresh token once and replays, rather than logging the
 *   user out mid-shift;
 * * simultaneous 401s share one rotation, because independent rotations look
 *   like token reuse and revoke every session the user has.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, getRefreshToken, hasAccessToken, setTokens } from "../api/client";
import { renderApp5 } from "../test/render5";
import { authUser, installApi5, problem, tokens } from "../test/server5";

beforeEach(() => {
  setTokens(null, null);
  sessionStorage.clear();
  localStorage.clear();
});

afterEach(() => vi.unstubAllGlobals());

describe("signing in", () => {
  it("sends the user to their worklist", async () => {
    installApi5();
    renderApp5("/login", { signedIn: false });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/Employee code/i), "D-1001");
    await user.type(screen.getByLabelText(/Password/i), "Correct-Horse-Battery-9");
    await user.click(screen.getByRole("button", { name: /Sign in/i }));

    expect(await screen.findByText(/My open flags|Department flags/i)).toBeInTheDocument();
  });

  it("never writes the access token to localStorage", async () => {
    // A token there is readable by any script on the page and survives the tab
    // closing on a shared ward computer.
    installApi5();
    renderApp5("/login", { signedIn: false });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/Employee code/i), "D-1001");
    await user.type(screen.getByLabelText(/Password/i), "Correct-Horse-Battery-9");
    await user.click(screen.getByRole("button", { name: /Sign in/i }));

    await waitFor(() => expect(hasAccessToken()).toBe(true));
    expect(JSON.stringify(localStorage)).not.toContain("access-token-1");
    expect(localStorage.length).toBe(0);
  });

  it("shows the server's one message for every kind of failure", async () => {
    installApi5({
      login: () =>
        problem(
          401,
          "Invalid employee code or password, or the account is unavailable.",
        ),
    });
    renderApp5("/login", { signedIn: false });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/Employee code/i), "D-1001");
    await user.type(screen.getByLabelText(/Password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /Sign in/i }));

    expect(
      await screen.findByText(/Invalid employee code or password/i),
    ).toBeInTheDocument();
  });

  it("clears the password but keeps the employee code after a failure", async () => {
    // Retyping the code on every attempt is how people end up locked out.
    installApi5({ login: () => problem(401, "Invalid employee code or password.") });
    renderApp5("/login", { signedIn: false });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/Employee code/i), "D-1001");
    await user.type(screen.getByLabelText(/Password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /Sign in/i }));

    await screen.findByText(/Invalid employee code/i);
    expect(screen.getByLabelText(/Employee code/i)).toHaveValue("D-1001");
    expect(screen.getByLabelText(/Password/i)).toHaveValue("");
  });

  it("mentions the lockout so a locked-out user knows what happened", async () => {
    installApi5();
    renderApp5("/login", { signedIn: false });

    expect(
      screen.getByText(/Five failed attempts lock an account for 15 minutes/i),
    ).toBeInTheDocument();
  });
});

describe("being kept out", () => {
  it("redirects an unauthenticated visitor to the login page", async () => {
    installApi5();
    renderApp5("/worklist", { signedIn: false });

    expect(await screen.findByRole("button", { name: /Sign in/i })).toBeInTheDocument();
  });

  it("sends a user who must change their password to that page", async () => {
    installApi5({
      refresh: () =>
        tokens({
          must_change_password: true,
          user: authUser({ must_change_password: true }),
        }),
    });
    renderApp5("/worklist");

    expect(await screen.findByText(/Change your password/i)).toBeInTheDocument();
  });

  it("explains a role refusal instead of showing an empty page", async () => {
    installApi5();
    renderApp5("/admin");

    expect(
      await screen.findByText(/You do not have access to this page/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/You are signed in as/i)).toBeInTheDocument();
  });

  it("does not offer an admin link to a doctor", async () => {
    installApi5();
    renderApp5("/worklist");

    await screen.findByText(/My open flags|Department flags/i);
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit trail" })).not.toBeInTheDocument();
  });

  it("offers the audit link to an auditor", async () => {
    installApi5({
      refresh: () => tokens({ user: authUser({ role: "auditor" }) }),
    });
    renderApp5("/worklist");

    expect(await screen.findByRole("link", { name: "Audit trail" })).toBeInTheDocument();
  });
});

describe("staying signed in", () => {
  it("restores the session from the refresh token after a reload", async () => {
    installApi5();
    renderApp5("/worklist");

    expect(await screen.findByText("Asha Menon")).toBeInTheDocument();
  });

  it("rotates once and replays when the access token expires", async () => {
    let firstCall = true;
    const api = installApi5({
      worklist: () => {
        if (firstCall) {
          firstCall = false;
          return problem(401, "Invalid or expired token");
        }
        return { rows: [], next_cursor: null, total_open: 0 };
      },
    });
    renderApp5("/worklist");

    // It recovers rather than bouncing the doctor to a login form mid-shift.
    await screen.findByText(/Nothing is waiting for you/i);
    const worklistCalls = api.getsTo("/worklist");
    expect(worklistCalls.length).toBeGreaterThanOrEqual(2);
  });

  it("signs the user out when the refresh token is rejected", async () => {
    const api = installApi5({
      refresh: () => problem(401, "Refresh token is not valid."),
    });
    renderApp5("/worklist");

    expect(await screen.findByRole("button", { name: /Sign in/i })).toBeInTheDocument();
    expect(api.calls.some((call) => call.path === "/auth/refresh")).toBe(true);
  });

  it("logs out locally even when the server cannot be reached", async () => {
    // Refusing to log out because the network is down would leave a ward
    // computer signed in.
    installApi5();
    renderApp5("/worklist");
    const user = userEvent.setup();

    await screen.findByText("Asha Menon");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      }),
    );

    await user.click(screen.getByRole("button", { name: /Sign out/i }));
    await waitFor(() => expect(hasAccessToken()).toBe(false));
    expect(getRefreshToken()).toBeNull();
  });
});

describe("concurrent expiry", () => {
  it("shares one refresh across simultaneous 401s", async () => {
    // Independent rotations present the same refresh token twice, which the
    // server reads as theft and answers by revoking every session the user
    // has. One shared promise is what stops a burst of polls doing that.
    let refreshes = 0;
    let failures = 3;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method ?? "GET").toUpperCase();

        if (method === "POST" && url.endsWith("/auth/refresh")) {
          refreshes += 1;
          return new Response(
            JSON.stringify({ access_token: "new", refresh_token: "new-r" }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (failures > 0) {
          failures -= 1;
          return new Response(JSON.stringify({ title: "expired", status: 401 }), {
            status: 401,
            headers: { "Content-Type": "application/problem+json" },
          });
        }
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    setTokens("stale", "refresh-1");
    await Promise.all([
      api.get("/worklist"),
      api.get("/cases/a/detail"),
      api.get("/health"),
    ]);

    expect(refreshes).toBe(1);
  });
});
