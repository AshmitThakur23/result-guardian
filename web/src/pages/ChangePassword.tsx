/**
 * Change your password. Phase 5.1.
 *
 * The only page reachable while `must_change_password` is set — the server
 * refuses every other route with a 403, and this mirrors it so the user sees
 * a form rather than a wall of permission errors.
 *
 * The policy is restated on screen rather than discovered through rejections.
 * A rule the user only learns by failing it is a rule that produces
 * `Password1!` on the third attempt.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";

const MIN_LENGTH = 12;

export function ChangePasswordPage() {
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (mismatch) return;
    setError(null);
    setSubmitting(true);
    try {
      await api.post("/auth/change-password", {
        current_password: current,
        new_password: next,
      });
      // Every other session was revoked server-side; this one keeps its
      // access token until it expires, so refresh the user to clear the
      // must-change flag without a re-login.
      await refreshUser();
      navigate("/worklist", { replace: true });
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message_for_user
          : "Could not change the password.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4">
      <h1 className="text-2xl font-semibold text-ink">Choose a new password</h1>
      {user?.must_change_password ? (
        <p className="mt-1 text-sm text-ink-body">
          You must change your password before using the system.
        </p>
      ) : null}

      <form onSubmit={onSubmit} className="mt-8 space-y-4">
        {error ? (
          <Banner tone="danger" title="Could not change the password">
            <p>{error}</p>
          </Banner>
        ) : null}

        <label className="block">
          <span className="text-sm font-medium text-ink">Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-ink">New password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_LENGTH}
            value={next}
            onChange={(event) => setNext(event.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-ink">Confirm new password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            aria-invalid={mismatch}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
          />
          {mismatch ? (
            <span className="mt-1 block text-sm text-critical-text">
              The two passwords do not match.
            </span>
          ) : null}
        </label>

        <ul className="list-inside list-disc text-xs text-ink-body">
          <li>At least {MIN_LENGTH} characters</li>
          <li>Upper and lower case letters, and a digit</li>
          <li>Must not contain your employee code</li>
        </ul>

        <Button type="submit" disabled={submitting || mismatch} className="w-full">
          {submitting ? "Saving…" : "Change password"}
        </Button>

        <p className="text-xs text-ink-muted">
          Changing your password signs you out of every other device.
        </p>
      </form>
    </main>
  );
}
