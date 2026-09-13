/**
 * The one place this app talks to NODE A.
 *
 * Everything goes through a same-origin `/api` prefix -- Caddy serves the
 * built bundle and proxies `/api` to the API container, and Vite's dev server
 * proxies the same path. There is deliberately no configurable base URL: a
 * build-time host would be one more thing that can point a hospital's
 * dashboard at the wrong machine.
 */

import type { ContractViolation } from "./types";

/** An RFC 7807 problem+json response, kept whole. */
export interface Problem {
  type?: string;
  title: string;
  status: number;
  detail?: string;
  /** Extension member from the contracts endpoint's 422. */
  violations?: ContractViolation[];
  /** Extension member from the discharge endpoint's 409. */
  blocking_orders?: unknown[];
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.title);
    this.name = "ApiError";
    this.status = problem.status;
    this.problem = problem;
  }

  get violations(): ContractViolation[] {
    return Array.isArray(this.problem.violations) ? this.problem.violations : [];
  }

  /** What to put in front of a doctor. Never a status code on its own. */
  get message_for_user(): string {
    return this.problem.detail
      ? `${this.problem.title} — ${this.problem.detail}`
      : this.problem.title;
  }
}

async function toProblem(response: Response): Promise<Problem> {
  // A proxy timeout or a crashed container answers with HTML, not
  // problem+json. Falling through to a readable sentence matters more here
  // than anywhere else in the product: the alternative is a doctor staring at
  // "Unexpected token < in JSON".
  try {
    const body = (await response.json()) as Partial<Problem>;
    if (body && typeof body.title === "string") {
      return { ...body, title: body.title, status: response.status };
    }
  } catch {
    /* fall through */
  }
  return {
    title:
      response.status >= 500
        ? "The server could not complete this request"
        : "The request was refused",
    status: response.status,
  };
}

/**
 * Phase 5.1 — the access token, held in memory only.
 *
 * **Not in `localStorage`.** A token there is readable by any script that
 * gets onto the page, and survives the tab being closed on a shared ward
 * computer. In memory it dies with the tab, which is the correct lifetime for
 * a credential to a system holding patient data. The cost is a re-login after
 * a full page refresh; the refresh token in `sessionStorage` covers the
 * common case of a reload without making the 12-hour credential durable
 * across a browser restart.
 */
let accessToken: string | null = null;
const REFRESH_KEY = "rg.refresh";

export function setTokens(access: string | null, refresh?: string | null): void {
  accessToken = access;
  try {
    if (refresh === null) sessionStorage.removeItem(REFRESH_KEY);
    else if (refresh !== undefined) sessionStorage.setItem(REFRESH_KEY, refresh);
  } catch {
    // Private mode, or storage disabled. The app still works for this tab --
    // it just cannot survive a reload, which is a degradation, not a failure.
  }
}

export function getRefreshToken(): string | null {
  try {
    return sessionStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

/** Called when a refresh fails: the session is over, wherever we are. */
let onSessionLost: (() => void) | null = null;
export function setSessionLostHandler(handler: (() => void) | null): void {
  onSessionLost = handler;
}

/**
 * Refresh, with a single in-flight promise shared by every caller.
 *
 * The dashboard polls every 30s and the case page loads several queries at
 * once, so an expired access token produces a burst of simultaneous 401s.
 * Without this, each would rotate the refresh token independently and all but
 * the first would be treated as **token reuse** — which revokes every session
 * the user has. One shared promise turns that into one rotation.
 */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const refresh = getRefreshToken();
    if (!refresh) return false;
    try {
      const response = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) {
        setTokens(null, null);
        return false;
      }
      const body = (await response.json()) as {
        access_token: string;
        refresh_token: string;
      };
      setTokens(body.access_token, body.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

async function request<T>(path: string, init?: RequestInit, retrying = false): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...init?.headers,
      },
    });
  } catch (cause) {
    // Network-level failure: NODE A is unreachable from this browser. Not a
    // problem+json, because nothing answered.
    throw new ApiError({
      title: "Cannot reach the Result Guardian server",
      detail: "Check the network connection and try again.",
      status: 0,
      cause: String(cause),
    });
  }

  // A 401 on an authenticated call means the 15-minute access token expired.
  // Rotate once and replay; never loop, or a genuinely revoked session would
  // spin. The auth endpoints are excluded: a 401 from /auth/login is the
  // answer, not a stale token.
  if (
    response.status === 401 &&
    !retrying &&
    accessToken !== null &&
    !path.startsWith("/auth/")
  ) {
    if (await refreshAccessToken()) return request<T>(path, init, true);
    setTokens(null, null);
    onSessionLost?.();
  }

  if (!response.ok) throw new ApiError(await toProblem(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      // The discharge action takes no body at all, and sending `{}` where the
      // server expects nothing is a needless difference from the contract.
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  /**
   * A file download. Goes through the same auth path as everything else --
   * a plain `<a href>` would not carry the Authorization header, and putting
   * the token in a query string would write a live credential into every
   * proxy log between here and the server.
   */
  download: async (path: string, filename: string): Promise<void> => {
    const response = await fetch(`/api${path}`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    });
    if (!response.ok) throw new ApiError(await toProblem(response));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  },
};
