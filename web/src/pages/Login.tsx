/**
 * Sign in. Phase 5.1.
 *
 * The failure message is deliberately the server's — one sentence covering
 * an unknown code, a wrong password, a locked account and a deactivated one.
 * Helpfully distinguishing them here would rebuild the account-enumeration
 * oracle the API goes out of its way not to be.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";

export function LoginPage() {
  const { user, login, restoring } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/worklist";

  const [employeeCode, setEmployeeCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (restoring) {
    return (
      <p role="status" className="py-16 text-center text-sm text-ink-body">
        Restoring your session…
      </p>
    );
  }
  if (user) return <Navigate to={from} replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const signedIn = await login(employeeCode.trim(), password);
      navigate(signedIn.must_change_password ? "/change-password" : from, {
        replace: true,
      });
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message_for_user
          : "Could not sign in. Check the network connection.",
      );
      // Clear the password, not the employee code: the code is almost always
      // right and retyping it on every attempt is how people end up locked out.
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4">
      <h1 className="text-2xl font-semibold text-ink">Result Guardian</h1>
      <p className="mt-1 text-sm text-ink-body">
        Post-discharge investigation follow-up
      </p>

      <form onSubmit={onSubmit} className="mt-8 space-y-4" aria-describedby="login-error">
        {error ? (
          <div id="login-error">
            <Banner tone="danger" title="Sign in failed">
              <p>{error}</p>
            </Banner>
          </div>
        ) : null}

        <label className="block">
          <span className="text-sm font-medium text-ink">Employee code</span>
          <input
            name="employee_code"
            autoComplete="username"
            required
            autoFocus
            value={employeeCode}
            onChange={(event) => setEmployeeCode(event.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-ink">Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
          />
        </label>

        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>

      <p className="mt-6 text-xs text-ink-muted">
        Five failed attempts lock an account for 15 minutes. If you are locked
        out, an administrator can unlock it.
      </p>
    </main>
  );
}
