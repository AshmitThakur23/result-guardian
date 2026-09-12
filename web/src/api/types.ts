/**
 * Mirrors of the Phase 1.3 response schemas in `api/app/schemas/`.
 *
 * Hand-written rather than generated: the surface is eight endpoints, and a
 * generator would also drag in every field the gate deliberately does not
 * render. If the backend shapes change, `web/src/api/types.test.ts` is the
 * thing that should be updated alongside them.
 */

export interface UserSummary {
  id: string;
  employee_code: string;
  full_name: string;
  role: string;
  is_active: boolean;
  department_id: string | null;
}

export interface PatientSummary {
  id: string;
  mrn: string;
  name: string;
}

export interface EncounterDetail {
  id: string;
  encounter_no: string;
  type: string;
  status: string;
  admitted_at: string;
  discharged_at: string | null;
  ward: string | null;
  bed: string | null;
  department_id: string | null;
  patient: PatientSummary;
  attending_doctor: UserSummary | null;
}

export interface BlockingOrder {
  order_id: string;
  test_code: string;
  test_name: string;
  category: string;
  status: string;
  ordered_at: string;
  /** NUMERIC on the wire, so it arrives as a JSON string like "24.00". */
  expected_tat_hours: string | number | null;
  /** ordered_at + TAT. Null when the order carries no TAT at all. */
  suggested_expected_by: string | null;
}

export interface ContractedOrder {
  order_id: string;
  test_code: string;
  test_name: string;
  status: string;
  contract_id: string;
  responsible_doctor_id: string;
  expected_by: string;
}

export interface DischargeReadiness {
  encounter_id: string;
  encounter_type: string;
  gate_applies: boolean;
  can_discharge: boolean;
  blocking_orders: BlockingOrder[];
  already_contracted: ContractedOrder[];
}

export interface DischargeContractRequest {
  order_id: string;
  responsible_doctor_id: string;
  /** Aware ISO-8601. The backend rejects a naive datetime outright. */
  expected_by: string;
  note?: string | null;
}

export interface CreatedContract {
  contract_id: string;
  order_id: string;
  encounter_id: string;
  responsible_doctor_id: string;
  expected_by: string;
  note: string | null;
}

export interface DischargeContractsCreated {
  encounter_id: string;
  created: CreatedContract[];
}

export interface OpenedCase {
  case_id: string;
  order_id: string;
  contract_id: string;
  current_owner_id: string;
  expected_by: string;
  timer_msg_id: number;
}

export interface DischargeResult {
  encounter_id: string;
  status: string;
  discharged_at: string;
  opened_cases: OpenedCase[];
}

export const OVERRIDE_REASON_CODES = [
  "patient_lama",
  "transfer_out",
  "deceased",
  "system_outage",
  "clinical_urgency",
] as const;

export type OverrideReasonCode = (typeof OVERRIDE_REASON_CODES)[number];

/** Matches `MIN_OVERRIDE_REASON_CHARS` in `api/app/schemas/discharge.py`. */
export const MIN_OVERRIDE_REASON_CHARS = 20;

export const OVERRIDE_REASON_LABELS: Record<OverrideReasonCode, string> = {
  patient_lama: "Patient left against medical advice",
  transfer_out: "Transferred to another facility",
  deceased: "Patient deceased",
  system_outage: "System outage",
  clinical_urgency: "Clinical urgency",
};

export interface DischargeOverrideRequest {
  reason_code: OverrideReasonCode;
  reason_text: string;
  overridden_by: string;
  approved_by?: string | null;
}

export interface CreatedOverride {
  override_id: string;
  order_id: string;
  case_id: string;
  flagged_owner_id: string;
}

export interface DischargeOverrideResult {
  encounter_id: string;
  status: string;
  discharged_at: string;
  reason_code: string;
  unit_head_id: string;
  overridden: CreatedOverride[];
  opened_cases: OpenedCase[];
}

/** One entry of the 422 violation list the contracts endpoint returns. */
export interface ContractViolation {
  order_id: string | null;
  code: string;
  detail: string;
}
