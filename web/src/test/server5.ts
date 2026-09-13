/**
 * A stand-in for NODE A's Phase 5 endpoints.
 *
 * Follows the same shape as `server.ts`: `fetch` is stubbed and every call is
 * recorded, because these tests care as much about *which requests were made*
 * as about what rendered — that a bulk close never reaches the server when a
 * critical case is selected, and that the Authorization header is present on
 * every authenticated call.
 */

import { vi } from "vitest";

import type {
  AuthUser,
  CaseDetail,
  HealthStatus,
  TokenResponse,
  WorklistPage,
  WorklistRow,
} from "../api/types5";

export const DOCTOR_ID = "11111111-1111-4111-8111-111111111111";
export const CASE_ID = "55555555-5555-4555-8555-555555555555";
export const CRITICAL_CASE_ID = "66666666-6666-4666-8666-666666666666";
export const PATIENT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";

const HOUR = 60 * 60 * 1000;

export function authUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: DOCTOR_ID,
    employee_code: "D-1001",
    full_name: "Asha Menon",
    role: "doctor",
    department_id: "99999999-9999-4999-8999-999999999999",
    must_change_password: false,
    break_glass: false,
    ...overrides,
  };
}

export function tokens(overrides: Partial<TokenResponse> = {}): TokenResponse {
  return {
    access_token: "access-token-1",
    refresh_token: "refresh-token-1",
    token_type: "bearer",
    expires_in: 900,
    must_change_password: false,
    user: authUser(),
    ...overrides,
  };
}

export function worklistRow(overrides: Partial<WorklistRow> = {}): WorklistRow {
  return {
    case_id: CASE_ID,
    patient_id: PATIENT_ID,
    patient_name: "Sunita Rao",
    mrn: "MRN-77021",
    test_name: "Urine Culture",
    severity: "follow_up",
    state: "flagged",
    opened_at: new Date(Date.now() - 6 * HOUR).toISOString(),
    flagged_at: new Date(Date.now() - 5 * HOUR).toISOString(),
    age_seconds: 5 * 3600,
    escalation_level: 0,
    next_escalation_at: new Date(Date.now() + 2 * HOUR).toISOString(),
    seconds_to_next_escalation: 2 * 3600,
    owner_id: DOCTOR_ID,
    owner_name: "Asha Menon",
    department_id: "99999999-9999-4999-8999-999999999999",
    department_name: "Medicine",
    summary: "Follow-up — Creatinine is 1.9 mg/dL, above the reference range 0.6–1.3.",
    reopened_count: 0,
    ...overrides,
  };
}

export function criticalRow(overrides: Partial<WorklistRow> = {}): WorklistRow {
  return worklistRow({
    case_id: CRITICAL_CASE_ID,
    patient_name: "Imran Qureshi",
    mrn: "MRN-88132",
    severity: "critical",
    test_name: "Potassium",
    summary:
      "CRITICAL — Potassium is 7.2 mmol/L — above the critical threshold of 6.5.",
    seconds_to_next_escalation: -1800,
    escalation_level: 2,
    ...overrides,
  });
}

export function caseDetail(overrides: Partial<CaseDetail> = {}): CaseDetail {
  return {
    case_id: CASE_ID,
    state: "flagged",
    severity: "critical",
    opened_at: new Date(Date.now() - 6 * HOUR).toISOString(),
    flagged_at: new Date(Date.now() - 5 * HOUR).toISOString(),
    acknowledged_at: null,
    closed_at: null,
    closure_reason: null,
    closure_note: null,
    reopened_count: 0,
    patient: {
      id: PATIENT_ID,
      full_name: "Sunita Rao",
      mrn: "MRN-77021",
      sex: "F",
      dob: "1978-04-02",
    },
    encounter: {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      department_name: "Medicine",
      discharged_at: new Date(Date.now() - 24 * HOUR).toISOString(),
      status: "discharged",
    },
    order: { id: "order-1", test_name: "Urine Culture", test_code: "URC" },
    owner: { id: DOCTOR_ID, full_name: "Asha Menon", employee_code: "D-1001" },
    result: {
      id: "result-1",
      report_status: "final",
      received_at: new Date(Date.now() - 4 * HOUR).toISOString(),
      source: "manual",
    },
    analytes: [
      {
        seq: 1,
        test_name: "Potassium",
        value: "7.2",
        unit: "mmol/L",
        ref_low: "3.5",
        ref_high: "5.1",
        ref_text: null,
        abnormal: true,
        abnormal_direction: "high",
        lab_flag: "H",
      },
      {
        seq: 2,
        test_name: "Sodium",
        value: "140",
        unit: "mmol/L",
        ref_low: "135",
        ref_high: "145",
        ref_text: null,
        abnormal: false,
        abnormal_direction: null,
        lab_flag: null,
      },
    ],
    organisms: [
      {
        organism: "E. coli",
        colony_count: ">100000 CFU/mL",
        specimen_type: "urine",
        sensitivities: [
          { antibiotic: "Amoxicillin-clavulanate", interpretation: "R", mic: null },
          { antibiotic: "Nitrofurantoin", interpretation: "S", mic: null },
        ],
      },
    ],
    narratives: [{ section: "impression", text: "Significant growth of E. coli." }],
    explanations: [
      {
        severity: "critical",
        rule_id: "B",
        reason_code: "CULT_RESISTANT_TO_DISCHARGE_DRUG",
        headline:
          "Amoxicillin-clavulanate (discharge medication) is Resistant for E. coli.",
        detail:
          "The patient went home on an antibiotic this organism is resistant to. " +
          "The prescription needs review.",
      },
    ],
    timeline: [
      {
        occurred_at: new Date(Date.now() - 6 * HOUR).toISOString(),
        event_type: "case_opened",
        actor_name: null,
        payload: {},
      },
      {
        occurred_at: new Date(Date.now() - 5 * HOUR).toISOString(),
        event_type: "case_classified",
        actor_name: null,
        payload: {},
      },
    ],
    escalation_level: 1,
    next_escalation_at: new Date(Date.now() + HOUR).toISOString(),
    can_acknowledge: true,
    ...overrides,
  };
}

export function health(overrides: Partial<HealthStatus> = {}): HealthStatus {
  return {
    status: "ok",
    db: "ok",
    version: "0.1.0",
    worker_heartbeat_age_s: 5,
    llm: { reachable: false, error: "ConnectError" },
    degraded_features: ["llm_generation"],
    ...overrides,
  };
}

interface Call {
  method: string;
  path: string;
  body: unknown;
  authorization: string | null;
}

interface Handlers {
  login?: (body: Record<string, unknown>) => unknown;
  refresh?: () => unknown;
  me?: () => unknown;
  // Returns a page, or a `problem()` the stub turns into an error response.
  worklist?: (search: URLSearchParams) => WorklistPage | ReturnType<typeof problem>;
  caseDetail?: () => unknown;
  close?: (body: Record<string, unknown>) => unknown;
  bulkClose?: (body: Record<string, unknown>) => unknown;
  reopen?: (body: Record<string, unknown>) => unknown;
  note?: (body: Record<string, unknown>) => unknown;
  health?: () => HealthStatus;
  verify?: () => unknown;
  audit?: () => unknown;
  nodeB?: () => unknown;
  killSwitch?: (body: Record<string, unknown>) => unknown;
}

/** A response the stub turns into a problem+json error. */
export function problem(status: number, title: string, extra: object = {}) {
  return { __problem: true as const, status, title, ...extra };
}

function isProblem(value: unknown): value is { __problem: true; status: number } {
  return typeof value === "object" && value !== null && "__problem" in value;
}

export function installApi5(handlers: Handlers = {}) {
  const calls: Call[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.replace(/^\/api/, "");
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const headers = new Headers(init?.headers as HeadersInit | undefined);
    calls.push({
      method,
      path,
      body,
      authorization: headers.get("Authorization"),
    });

    const respond = (value: unknown, status = 200) =>
      new Response(status === 204 ? null : JSON.stringify(value), {
        status,
        headers: { "Content-Type": "application/json" },
      });

    const result = (value: unknown, okStatus = 200) => {
      if (isProblem(value)) {
        const { __problem: _flag, ...rest } = value;
        void _flag;
        return new Response(JSON.stringify({ type: "about:blank", ...rest }), {
          status: value.status,
          headers: { "Content-Type": "application/problem+json" },
        });
      }
      return respond(value, okStatus);
    };

    const search = new URL(url, "http://t").searchParams;
    const bare = path.split("?")[0];

    if (method === "POST" && bare === "/auth/login") {
      return result(handlers.login?.(body ?? {}) ?? tokens());
    }
    if (method === "POST" && bare === "/auth/refresh") {
      return result(handlers.refresh?.() ?? tokens());
    }
    if (method === "POST" && bare === "/auth/logout") {
      return respond({ sessions_revoked: 1 });
    }
    if (method === "GET" && bare === "/auth/me") {
      return result(handlers.me?.() ?? authUser());
    }
    if (method === "GET" && bare === "/health") {
      return respond(handlers.health?.() ?? health());
    }
    if (method === "GET" && bare === "/worklist") {
      return result(
        handlers.worklist?.(search) ?? {
          rows: [worklistRow()],
          next_cursor: null,
          total_open: 1,
        },
      );
    }
    if (method === "GET" && /^\/cases\/[^/]+\/detail$/.test(bare)) {
      return result(handlers.caseDetail?.() ?? caseDetail());
    }
    if (method === "POST" && bare === "/cases/bulk-close") {
      return result(handlers.bulkClose?.(body ?? {}) ?? { closed: [] });
    }
    if (method === "POST" && /\/close$/.test(bare)) {
      return result(
        handlers.close?.(body ?? {}) ?? {
          case_id: CASE_ID,
          closure_reason: String((body as Record<string, unknown>)?.closure_reason),
          already_closed: false,
          cancelled_timer_ids: [],
          audit_seq: 42,
        },
      );
    }
    if (method === "POST" && /\/reopen$/.test(bare)) {
      return result(
        handlers.reopen?.(body ?? {}) ?? {
          case_id: CASE_ID,
          reopened_count: 1,
          reason: String((body as Record<string, unknown>)?.reason),
        },
      );
    }
    if (method === "POST" && /\/notes$/.test(bare)) {
      return result(handlers.note?.(body ?? {}) ?? { case_id: CASE_ID }, 201);
    }
    if (method === "GET" && bare === "/audit/verify") {
      return result(
        handlers.verify?.() ?? {
          intact: true,
          rows_checked: 120,
          first_break_seq: null,
          first_break_reason: null,
          head_seq: 120,
          head_hash: "a".repeat(64),
          window_anchored: true,
          verified_at: new Date().toISOString(),
        },
      );
    }
    if (method === "GET" && bare === "/audit/anchors") {
      return respond([]);
    }
    if (method === "GET" && bare === "/audit") {
      return result(handlers.audit?.() ?? { rows: [], next_after_seq: null });
    }
    if (method === "GET" && bare === "/admin/node-b") {
      return result(
        handlers.nodeB?.() ?? {
          llm_enabled: true,
          llm_enabled_source: "environment",
          kill_switch_reason: null,
          reachable: false,
          base_url: "http://192.168.1.50:11434",
          model: "qwen3:4b",
          probe_error: "ConnectError",
          safety_note:
            "NODE B is an accelerator. Every safety guarantee — tracking, " +
            "timers, escalation, notification — runs on NODE A and is " +
            "unaffected when this is off or unreachable.",
        },
      );
    }
    if (method === "POST" && bare === "/admin/node-b/kill-switch") {
      return result(handlers.killSwitch?.(body ?? {}) ?? { llm_enabled: false });
    }

    return respond({ title: `No handler for ${method} ${bare}`, status: 404 }, 404);
  });

  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    postsTo: (suffix: string) =>
      calls.filter((call) => call.method === "POST" && call.path.endsWith(suffix)),
    getsTo: (prefix: string) =>
      calls.filter((call) => call.method === "GET" && call.path.startsWith(prefix)),
  };
}
