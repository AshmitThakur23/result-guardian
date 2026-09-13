/**
 * Who is signed in, and what they may see. Phase 5.1.
 *
 * The server is the authority on both — `require_role` and `scope_clause`
 * decide every request, and nothing here can widen that. What this provides
 * is the *interface* half: not rendering an admin link a doctor would only
 * get a 403 from, and sending someone who must change their password to the
 * one page that will let them.
 *
 * **A hidden button is not a permission check.** Every guard here is a
 * courtesy to the user; the real one is in `app/security.py`.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { api, getRefreshToken, setSessionLostHandler, setTokens } from "../api/client";
import type { AuthUser, TokenResponse } from "../api/types5";

interface AuthState {
  user: AuthUser | null;
  /** True until the initial silent refresh has settled. */
  restoring: boolean;
  login: (employeeCode: string, password: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
  breakGlass: (reason: string) => Promise<void>;
  /** Called after a password change so the banner clears without a reload. */
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [restoring, setRestoring] = useState(true);

  // A page reload loses the in-memory access token. If a refresh token
  // survived in sessionStorage, trade it for a new pair rather than bouncing
  // the user to a login form they filled in four minutes ago.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const refresh = getRefreshToken();
      if (!refresh) {
        setRestoring(false);
        return;
      }
      try {
        const tokens = await api.post<TokenResponse>("/auth/refresh", {
          refresh_token: refresh,
        });
        if (cancelled) return;
        setTokens(tokens.access_token, tokens.refresh_token);
        setUser(tokens.user);
      } catch {
        setTokens(null, null);
      } finally {
        if (!cancelled) setRestoring(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // When a refresh fails mid-session the client clears the tokens; this puts
  // the UI in the same state rather than leaving a shell rendered around
  // requests that will all 401.
  useEffect(() => {
    setSessionLostHandler(() => setUser(null));
    return () => setSessionLostHandler(null);
  }, []);

  const login = useCallback(async (employeeCode: string, password: string) => {
    const tokens = await api.post<TokenResponse>("/auth/login", {
      employee_code: employeeCode,
      password,
    });
    setTokens(tokens.access_token, tokens.refresh_token);
    setUser(tokens.user);
    return tokens.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout", { refresh_token: getRefreshToken() });
    } catch {
      // Already expired or the server is unreachable. Either way the local
      // session ends: refusing to log out because the network is down would
      // leave a ward computer signed in.
    }
    setTokens(null, null);
    setUser(null);
  }, []);

  const breakGlass = useCallback(async (reason: string) => {
    const tokens = await api.post<TokenResponse>("/auth/break-glass", { reason });
    setTokens(tokens.access_token, tokens.refresh_token);
    setUser(tokens.user);
  }, []);

  const refreshUser = useCallback(async () => {
    setUser(await api.get<AuthUser>("/auth/me"));
  }, []);

  const value = useMemo(
    () => ({ user, restoring, login, logout, breakGlass, refreshUser }),
    [user, restoring, login, logout, breakGlass, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Gate a route. `roles` empty means "any signed-in user".
 *
 * The `must_change_password` redirect comes first and applies to every route
 * except the change-password page itself — matching the server, which refuses
 * everything else with a 403.
 */
export function RequireAuth({
  children,
  roles = [],
}: {
  children: ReactNode;
  roles?: string[];
}) {
  const { user, restoring } = useAuth();
  const location = useLocation();

  if (restoring) {
    return (
      <p role="status" className="py-16 text-center text-sm text-slate-600">
        Restoring your session…
      </p>
    );
  }

  if (!user) {
    // `state.from` so the login page can send them back where they were
    // going, which matters when a link to a case is what brought them here.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (user.must_change_password && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }

  if (roles.length > 0 && !roles.includes(user.role)) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-16 text-center">
        <h1 className="text-xl font-semibold text-slate-900">
          You do not have access to this page
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          This page is for: {roles.join(", ")}. You are signed in as{" "}
          <strong>{user.role.replace("_", " ")}</strong>.
        </p>
      </main>
    );
  }

  return <>{children}</>;
}

/** Show children only for these roles. For navigation, not for security. */
export function IfRole({ roles, children }: { roles: string[]; children: ReactNode }) {
  const { user } = useAuth();
  if (!user || !roles.includes(user.role)) return null;
  return <>{children}</>;
}
