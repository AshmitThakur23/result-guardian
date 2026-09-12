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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
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
};
