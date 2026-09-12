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
      // `limit` is honoured, not ignored: on a real ward the doctor list is
      // longer than one page, and a previously chosen doctor falling outside
      // it is exactly the condition that broke draft restore in the browser.
      const limit = Number(search.get("limit") ?? "50");
      const matched = all
        .filter(
          (user) =>
            (!role || user.role === role) &&
            (!q ||
              user.full_name.toLowerCase().includes(q) ||
              user.employee_code.toLowerCase().includes(q)),
        )
        .sort((a, b) => a.full_name.localeCompare(b.full_name));
      return respond(matched.slice(0, limit));
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

/* ── Phase 1.5 supporting screens ─────────────────────────────────── */

export const PATIENT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";

export function patientRow(
  overrides: Partial<import("../api/types").PatientSearchRow> = {},
): import("../api/types").PatientSearchRow {
  return {
    id: PATIENT_ID,
    mrn: "MRN-77021",
    name: "Sunita Rao",
    dob: "1978-04-02",
    sex: "F",
    phone_primary_e164: "+915550000001",
    active_encounter_count: 1,
    ...overrides,
  };
}

export function encounterFullDetail(
  overrides: Partial<import("../api/types").EncounterFullDetail> = {},
): import("../api/types").EncounterFullDetail {
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
    patient: { id: PATIENT_ID, mrn: "MRN-77021", name: "Sunita Rao" },
    attending_doctor: DOCTOR,
    orders: [
      {
        id: ORDER_A,
        test_code: "URC",
        test_name: "Urine Culture",
        category: "micro",
        status: "in_lab",
        ordered_at: new Date(Date.now() - 6 * HOUR).toISOString(),
        sample_collected_at: null,
        expected_tat_hours: "48.00",
        external_order_id: null,
        is_outstanding: true,
        contract_id: null,
        responsible_doctor_id: null,
        responsible_doctor_name: null,
        expected_by: null,
      },
      {
        id: ORDER_B,
        test_code: "CXR",
        test_name: "Chest X-Ray",
        category: "radiology",
        status: "final",
        ordered_at: new Date(Date.now() - 30 * HOUR).toISOString(),
        sample_collected_at: null,
        expected_tat_hours: "2.00",
        external_order_id: "ACC-100234",
        is_outstanding: false,
        contract_id: null,
        responsible_doctor_id: null,
        responsible_doctor_name: null,
        expected_by: null,
      },
    ],
    medications: [],
    gate_applies: true,
    can_discharge: false,
    blocking_order_count: 1,
    can_add_orders: true,
    ...overrides,
  };
}

export interface Phase15Handlers {
  patientSearch?: (q: string) => import("../api/types").PatientSearchRow[] | Problemish;
  patient?: () => import("../api/types").PatientWithEncounters | Problemish;
  encounterDetail?: () =>
    | import("../api/types").EncounterFullDetail
    | Problemish;
  createOrder?: (body: unknown) =>
    | import("../api/types").OrderCreated
    | Problemish;
  addMedication?: (body: unknown) =>
    | import("../api/types").DischargeMedicationRow
    | Problemish;
  /* Phase 3.7 */
  resultPreview?: (body: unknown) =>
    | import("../api/types").ResultPreview
    | Problemish;
  recordResult?: (body: unknown) =>
    | import("../api/types").ResultRecorded
    | Problemish;
}

/** A preview reply, shaped like the real one. */
export function preview(
  overrides: Partial<import("../api/types").ResultPreview> = {},
): import("../api/types").ResultPreview {
  return {
    severity: "normal",
    engine_version: "3.0.0",
    would_auto_close: true,
    rules: [],
    discharge_antibiotics: [],
    ...overrides,
  };
}

/**
 * The Phase 1.5 screens talk to a different set of endpoints from the gate,
 * so they get their own stub rather than widening `installServer` with five
 * more optional handlers the gate tests would never use.
 */
export function installPhase15Server(handlers: Phase15Handlers = {}) {
  const calls: Call[] = [];

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
      if (typeof value === "object" && value !== null && "__problem" in value) {
        const problemValue = value as Problemish;
        const { __problem: _flag, ...rest } = problemValue;
        void _flag;
        return new Response(JSON.stringify({ type: "about:blank", ...rest }), {
          status: problemValue.status,
          headers: { "Content-Type": "application/problem+json" },
        });
      }
      return respond(value, okStatus);
    };

    if (method === "GET" && path.startsWith("/patients/")) {
      return result(
        handlers.patient?.() ?? {
          patient: patientRow(),
          encounters: [
            {
              id: ENCOUNTER_ID,
              encounter_no: "ENC-2026-0042",
              type: "ipd",
              status: "active",
              admitted_at: new Date(Date.now() - 72 * HOUR).toISOString(),
              discharged_at: null,
              ward: "Ward 3",
              bed: "B12",
            },
          ],
        },
      );
    }
    if (method === "GET" && path.startsWith("/patients")) {
      const q = new URL(url, "http://t").searchParams.get("q") ?? "";
      return result(handlers.patientSearch?.(q) ?? [patientRow()]);
    }
    if (method === "GET" && path.endsWith("/detail")) {
      return result(handlers.encounterDetail?.() ?? encounterFullDetail());
    }
    if (method === "POST" && path.endsWith("/orders")) {
      const created = handlers.createOrder?.(body);
      if (created) return result(created, 201);
      const fields = body as Record<string, string>;
      return result(
        {
          order: {
            id: "0aaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            test_code: fields.test_code,
            test_name: fields.test_name,
            category: fields.category,
            status: fields.status ?? "ordered",
            ordered_at: new Date().toISOString(),
            sample_collected_at: null,
            expected_tat_hours: fields.expected_tat_hours ?? null,
            external_order_id: fields.external_order_id ?? null,
            is_outstanding: true,
            contract_id: null,
            responsible_doctor_id: null,
            responsible_doctor_name: null,
            expected_by: null,
          },
          encounter_id: ENCOUNTER_ID,
          encounter_can_discharge: false,
          blocking_order_count: 2,
        },
        201,
      );
    }
    // Phase 3.7. Checked before "/results" so the longer path wins.
    if (method === "POST" && path.endsWith("/results/preview")) {
      return result(handlers.resultPreview?.(body) ?? preview());
    }
    if (method === "POST" && path.endsWith("/results")) {
      const recorded = handlers.recordResult?.(body);
      if (recorded) return result(recorded, 201);
      return result(
        {
          result_id: "0ccccccc-cccc-4ccc-8ccc-cccccccccccc",
          order_id: ORDER_A,
          case_id: null,
          case_state: "result_received",
          superseded_timer_ids: [],
          classification_enqueued: true,
          late: false,
        },
        201,
      );
    }
    if (method === "POST" && path.endsWith("/discharge-medications")) {
      const added = handlers.addMedication?.(body);
      if (added) return result(added, 201);
      const fields = body as Record<string, unknown>;
      return result(
        {
          id: "0bbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          encounter_id: ENCOUNTER_ID,
          drug_name: fields.drug_name,
          drug_code: null,
          atc_code: fields.atc_code ?? null,
          dose: fields.dose ?? null,
          route: fields.route ?? null,
          frequency: fields.frequency ?? null,
          duration_days: fields.duration_days ?? null,
          is_antibiotic: fields.is_antibiotic ?? false,
        },
        201,
      );
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
