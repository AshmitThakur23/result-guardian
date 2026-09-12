/**
 * A stand-in for NODE A.
 *
 * `fetch` is stubbed rather than a mock-service-worker installed: the surface
 * is five endpoints, and every test here cares about *which calls were made in
 * what order* -- creating contracts before discharging, and never twice --
 * which a recorded call log states more directly than request interception.
 */

import { vi } from "vitest";

import type {
  DischargeContractsCreated,
  DischargeOverrideResult,
  DischargeReadiness,
  DischargeResult,
  EncounterDetail,
  UserSummary,
} from "../api/types";

export const DOCTOR: UserSummary = {
  id: "11111111-1111-4111-8111-111111111111",
  employee_code: "D-1001",
  full_name: "Asha Menon",
  role: "doctor",
  is_active: true,
  department_id: null,
};

export const OTHER_DOCTOR: UserSummary = {
  id: "22222222-2222-4222-8222-222222222222",
  employee_code: "D-1002",
  full_name: "Ravi Kulkarni",
  role: "doctor",
  is_active: true,
  department_id: null,
};

export const UNIT_HEAD: UserSummary = {
  id: "33333333-3333-4333-8333-333333333333",
  employee_code: "D-0001",
  full_name: "Meera Iyer",
  role: "unit_head",
  is_active: true,
  department_id: null,
};

export const ENCOUNTER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const ORDER_A = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const ORDER_B = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const HOUR = 60 * 60 * 1000;

export function encounter(overrides: Partial<EncounterDetail> = {}): EncounterDetail {
  return {
    id: ENCOUNTER_ID,
    encounter_no: "ENC-2026-0042",
    type: "ipd",
    status: "active",
    admitted_at: new Date(Date.now() - 72 * HOUR).toISOString(),
    discharged_at: null,
    ward: "Ward 3",
    bed: "B12",
    department_id: null,
    patient: {
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      mrn: "MRN-77021",
      name: "Sunita Rao",
    },
    attending_doctor: DOCTOR,
    ...overrides,
  };
}

export function readiness(
  overrides: Partial<DischargeReadiness> = {},
): DischargeReadiness {
  return {
    encounter_id: ENCOUNTER_ID,
    encounter_type: "ipd",
    gate_applies: true,
    can_discharge: false,
    blocking_orders: [
      {
        order_id: ORDER_A,
        test_code: "URC",
        test_name: "Urine Culture",
        category: "lab",
        status: "in_lab",
        ordered_at: new Date(Date.now() - 6 * HOUR).toISOString(),
        expected_tat_hours: "48.00",
        suggested_expected_by: new Date(Date.now() + 42 * HOUR).toISOString(),
      },
      {
        order_id: ORDER_B,
        test_code: "HBA1C",
        test_name: "HbA1c",
        category: "lab",
        status: "ordered",
        ordered_at: new Date(Date.now() - 30 * HOUR).toISOString(),
        expected_tat_hours: "24.00",
        // Turnaround already elapsed: the gate must not pre-fill a past date.
        suggested_expected_by: new Date(Date.now() - 6 * HOUR).toISOString(),
      },
    ],
    already_contracted: [],
    ...overrides,
  };
}

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

export interface Problemish {
  __problem: true;
  status: number;
  title: string;
  detail?: string;
  violations?: { order_id: string | null; code: string; detail: string }[];
}

export interface Handlers {
  encounter?: () => EncounterDetail | Problemish;
  readiness?: () => DischargeReadiness | Problemish;
  users?: () => UserSummary[];
  createContracts?: (body: unknown) => DischargeContractsCreated | Problemish;
  discharge?: () => DischargeResult | Problemish;
  override?: (body: unknown) => DischargeOverrideResult | Problemish;
}

export function problem(
  status: number,
  title: string,
  extra: Partial<Problemish> = {},
): Problemish {
  return { __problem: true, status, title, ...extra };
}

function isProblem(value: unknown): value is Problemish {
  return typeof value === "object" && value !== null && "__problem" in value;
}

export function installServer(handlers: Handlers = {}) {
  const calls: Call[] = [];

  const defaultCreate = (body: unknown): DischargeContractsCreated => {
    const contracts = (body as { contracts: Array<Record<string, string>> }).contracts;
    return {
      encounter_id: ENCOUNTER_ID,
      created: contracts.map((contract, index) => ({
        contract_id: `ffffffff-ffff-4fff-8fff-00000000000${index}`,
        order_id: contract.order_id,
        encounter_id: ENCOUNTER_ID,
        responsible_doctor_id: contract.responsible_doctor_id,
        expected_by: contract.expected_by,
        note: null,
      })),
    };
  };

  const defaultDischarge = (): DischargeResult => ({
    encounter_id: ENCOUNTER_ID,
    status: "discharged",
    discharged_at: new Date().toISOString(),
    opened_cases: [],
  });

  const defaultOverride = (body: unknown): DischargeOverrideResult => ({
    encounter_id: ENCOUNTER_ID,
    status: "discharged",
    discharged_at: new Date().toISOString(),
    reason_code: (body as { reason_code: string }).reason_code,
    unit_head_id: UNIT_HEAD.id,
    overridden: [
      {
        override_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        order_id: ORDER_A,
        case_id: "e1111111-1111-4111-8111-111111111111",
        flagged_owner_id: UNIT_HEAD.id,
      },
    ],
    opened_cases: [],
  });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.replace(/^\/api/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });

    const respond = (payload: unknown, status = 200) =>
      new Response(JSON.stringify(payload), {
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

    if (method === "GET" && path.startsWith("/users")) {
      const all = handlers.users?.() ?? [DOCTOR, OTHER_DOCTOR, UNIT_HEAD];
      const search = new URL(url, "http://t").searchParams;
      const q = search.get("q")?.toLowerCase() ?? "";
      const role = search.get("role");
      return respond(
        all.filter(
          (user) =>
            (!role || user.role === role) &&
            (!q ||
              user.full_name.toLowerCase().includes(q) ||
              user.employee_code.toLowerCase().includes(q)),
        ),
      );
    }
    if (method === "GET" && path.endsWith("/discharge-readiness")) {
      return result(handlers.readiness?.() ?? readiness());
    }
    if (method === "GET" && /^\/encounters\/[^/]+$/.test(path)) {
      return result(handlers.encounter?.() ?? encounter());
    }
    if (method === "POST" && path.endsWith("/discharge-contracts")) {
      return result(handlers.createContracts?.(body) ?? defaultCreate(body), 201);
    }
    if (method === "POST" && path.endsWith("/discharge-overrides")) {
      return result(handlers.override?.(body) ?? defaultOverride(body), 201);
    }
    if (method === "POST" && path.endsWith("/discharge")) {
      return result(handlers.discharge?.() ?? defaultDischarge());
    }

    return respond({ title: "No handler", status: 404 }, 404);
  });

  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    postsTo: (suffix: string) =>
      calls.filter((call) => call.method === "POST" && call.path.endsWith(suffix)),
  };
}
