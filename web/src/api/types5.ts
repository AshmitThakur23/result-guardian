/**
 * Phase 5 wire types — auth, the worklist, closure, audit, admin, reports.
 *
 * A separate module from `types.ts` rather than another 300 lines appended to
 * it: Phases 1–4 describe the tracking domain, and Phase 5 describes the
 * people using it. Keeping them apart means a change to the dashboard cannot
 * accidentally edit the shape of a discharge contract.
 */

import type { Severity } from "./types";

export const USER_ROLES = ["doctor", "unit_head", "lab_tech", "admin", "auditor"] as const;
export type UserRole = (typeof USER_ROLES)[number];

export interface AuthUser {
  id: string;
  employee_code: string;
  full_name: string;
  role: UserRole;
  department_id: string | null;
  must_change_password: boolean;
  break_glass: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  must_change_password: boolean;
  user: AuthUser;
}

export interface WorklistRow {
  case_id: string;
  patient_id: string;
  patient_name: string;
  mrn: string;
  test_name: string;
  severity: Severity | null;
  state: string;
  opened_at: string;
  flagged_at: string | null;
  age_seconds: number;
  escalation_level: number | null;
  next_escalation_at: string | null;
  /** Negative means the rung is overdue. Not clamped — that is information. */
  seconds_to_next_escalation: number | null;
  owner_id: string | null;
  owner_name: string | null;
  department_id: string | null;
  department_name: string | null;
  summary: string;
  reopened_count: number;
}

export interface WorklistPage {
  rows: WorklistRow[];
  next_cursor: string | null;
  total_open: number | null;
}

export interface AnalyteRow {
  seq: number;
  test_name: string;
  value: string | null;
  unit: string | null;
  ref_low: string | null;
  ref_high: string | null;
  ref_text: string | null;
  abnormal: boolean;
  abnormal_direction: "high" | "low" | null;
  lab_flag: string | null;
}

export interface SensitivityCell {
  antibiotic: string | null;
  interpretation: string | null;
  mic: string | null;
}

export interface OrganismRow {
  organism: string;
  colony_count: string | null;
  specimen_type: string | null;
  sensitivities: SensitivityCell[];
}

export interface RuleExplanation {
  severity: Severity;
  rule_id: string;
  reason_code: string;
  /** The plain-language sentence. Rendered by plain code — never by AI. */
  headline: string;
  detail: string | null;
}

export interface TimelineEvent {
  occurred_at: string;
  event_type: string;
  actor_name: string | null;
  payload: Record<string, unknown>;
}

export interface CaseDetail {
  case_id: string;
  state: string;
  severity: Severity | null;
  opened_at: string;
  flagged_at: string | null;
  acknowledged_at: string | null;
  closed_at: string | null;
  closure_reason: string | null;
  closure_note: string | null;
  reopened_count: number;
  patient: Record<string, unknown>;
  encounter: Record<string, unknown>;
  order: Record<string, unknown>;
  owner: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  analytes: AnalyteRow[];
  organisms: OrganismRow[];
  narratives: { section: string; text: string }[];
  explanations: RuleExplanation[];
  timeline: TimelineEvent[];
  escalation_level: number | null;
  next_escalation_at: string | null;
  can_acknowledge: boolean;
}

export const CLOSURE_REASONS = [
  "action_taken",
  "already_handled",
  "not_clinically_relevant",
  "duplicate_report",
  "patient_uncontactable",
] as const;
export type ClosureReason = (typeof CLOSURE_REASONS)[number];

export const CLOSURE_REASON_LABELS: Record<ClosureReason, string> = {
  action_taken: "Action taken",
  already_handled: "Already handled",
  not_clinically_relevant: "Not clinically relevant",
  duplicate_report: "Duplicate report",
  patient_uncontactable: "Patient uncontactable",
};

/**
 * What each reason additionally asks for.
 *
 * The build plan attaches a specific addendum to each one, and a generic
 * "a note is required" gets a generic note back — which is exactly what makes
 * the closure-reason metric in 5.6 unreadable.
 */
export const CLOSURE_REASON_PROMPTS: Record<ClosureReason, string> = {
  action_taken: "What was done, and when?",
  already_handled: "Where and when was this already handled?",
  not_clinically_relevant: "Why is this not clinically relevant for this patient?",
  duplicate_report: "Which case does this duplicate?",
  patient_uncontactable: "What contact attempts were made?",
};

export interface CloseCaseResult {
  case_id: string;
  closure_reason: string;
  already_closed: boolean;
  cancelled_timer_ids: string[];
  audit_seq: number | null;
}

export interface ReopenResult {
  case_id: string;
  reopened_count: number;
  reason: string;
}

export interface AuditRow {
  seq: number;
  occurred_at: string;
  actor_user_id: string | null;
  actor_name: string | null;
  actor_employee_code: string | null;
  actor_ip: string | null;
  action: string;
  entity_type: string;
  entity_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  prev_hash: string;
  row_hash: string;
  break_glass_reason: string | null;
}

export interface AuditPage {
  rows: AuditRow[];
  next_after_seq: number | null;
}

export interface ChainVerification {
  intact: boolean;
  rows_checked: number;
  first_break_seq: number | null;
  first_break_reason: string | null;
  head_seq: number | null;
  head_hash: string | null;
  window_anchored: boolean;
  verified_at: string;
}

export interface AuditAnchor {
  anchored_at: string;
  head_seq: number;
  head_hash: string;
  rows_verified: number;
  chain_intact: boolean;
  first_break_seq: number | null;
}

export interface NodeBStatus {
  llm_enabled: boolean;
  llm_enabled_source: string;
  kill_switch_reason: string | null;
  reachable: boolean;
  base_url: string;
  model: string;
  probe_error: string | null;
  safety_note: string;
}

export interface HealthStatus {
  status: string;
  db: string;
  version: string;
  worker_heartbeat_age_s: number | null;
  llm: { reachable: boolean; error?: string };
  degraded_features: string[];
}

export interface AdminUserRow {
  id: string;
  employee_code: string;
  full_name: string;
  email: string | null;
  phone_e164: string | null;
  role: UserRole;
  department_id: string | null;
  department_name: string | null;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  locked_until: string | null;
  failed_login_count: number;
}

export interface ProviderHealthRow {
  channel: string;
  adapter: string;
  sent_24h: number;
  failed_24h: number;
  suppressed_24h: number;
  failure_rate: number;
  last_failure_at: string | null;
  last_error: string | null;
}

export interface KeywordRow {
  id: string;
  term: string;
  category: string;
  severity: string;
  requires_negation_check: boolean;
  active: boolean;
}

export interface PanicThresholdRow {
  id: string;
  test_code: string;
  loinc_code: string | null;
  sex: string;
  critical_low: string | null;
  critical_high: string | null;
  unit: string | null;
  source: string;
  effective_from: string;
  effective_to: string | null;
}

export interface EscalationRungRow {
  id: string;
  department_id: string | null;
  department_name: string | null;
  level: number;
  target_type: string;
  delay_minutes: number;
  channels: string[];
  severity: string;
  active: boolean;
}

export interface OverrideRow {
  id: string;
  encounter_id: string;
  order_id: string;
  patient_name: string | null;
  mrn: string | null;
  reason_code: string;
  reason_text: string;
  overridden_by_name: string | null;
  approved_by_name: string | null;
  created_at: string;
  department_name: string | null;
}

export interface TurnaroundStat {
  stage: string;
  p50_seconds: number | null;
  p90_seconds: number | null;
  sample_size: number;
}

export interface MetricsSummary {
  window_start: string;
  window_end: string;
  department_id: string | null;
  turnaround: TurnaroundStat[];
  age_buckets: {
    label: string;
    lower_hours: number;
    upper_hours: number | null;
    count: number;
    critical_count: number;
  }[];
  escalations: {
    department_id: string | null;
    department_name: string | null;
    rung: number | null;
    fired: number;
  }[];
  closure_reasons: { closure_reason: string; count: number; share: number }[];
  flag_rate: {
    discharges: number;
    flagged_cases: number;
    flags_per_100_discharges: number;
    critical_per_100_discharges: number;
  };
  patient_contacts: {
    notifications_sent: number;
    notifications_suppressed: number;
    notifications_failed: number;
    outbound_contacts: number;
    inbound_callbacks: number;
    callback_rate: number;
  };
  overrides: { total: number; by_reason: { reason_code: string; count: number }[] };
  lab_flags: {
    open_count: number;
    oldest_age_hours: number | null;
    by_type: { flag_type: string; count: number; oldest_raised_at: string | null }[];
  };
}

export interface DoctorAckRow {
  user_id: string;
  full_name: string;
  employee_code: string;
  cases_closed: number;
  open_cases: number;
  p50_ack_seconds: number | null;
  p90_ack_seconds: number | null;
}

export interface WorklistFilters {
  severity?: string[];
  state?: string[];
  department_id?: string;
  mine_only?: boolean;
  include_closed?: boolean;
  overdue_only?: boolean;
  search?: string;
}
