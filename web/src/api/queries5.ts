/**
 * TanStack Query hooks for the Phase 5 screens.
 *
 * The worklist polls every **30 seconds**, per 5.2: *"Real-time-ish updates:
 * TanStack Query polling every 30s (skip websockets for v1)."* That number is
 * a judgement, not an arbitrary default — a case escalates on a scale of
 * tens of minutes, so a doctor watching a list sees a change within one
 * refresh of it happening, and a ward of twenty browsers costs the API forty
 * requests a minute rather than twenty open sockets.
 */

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "./client";
import type {
  AdminUserRow,
  AuditAnchor,
  AuditPage,
  CaseDetail,
  ChainVerification,
  CloseCaseResult,
  DoctorAckRow,
  EscalationRungRow,
  HealthStatus,
  KeywordRow,
  MetricsSummary,
  NodeBStatus,
  OverrideRow,
  PanicThresholdRow,
  ProviderHealthRow,
  ReopenResult,
  WorklistFilters,
  WorklistPage,
} from "./types5";

/** 5.2: "TanStack Query polling every 30s (skip websockets for v1)." */
export const WORKLIST_POLL_MS = 30_000;

export const keys5 = {
  worklist: (filters: WorklistFilters, cursor: string | null) =>
    ["worklist", filters, cursor] as const,
  caseDetail: (id: string) => ["case", id, "detail"] as const,
  health: () => ["health"] as const,
  audit: (filters: Record<string, unknown>, afterSeq: number | null) =>
    ["audit", filters, afterSeq] as const,
  auditVerify: (from: string | null, to: string | null) =>
    ["audit", "verify", from, to] as const,
  anchors: () => ["audit", "anchors"] as const,
  nodeB: () => ["admin", "node-b"] as const,
  adminUsers: () => ["admin", "users"] as const,
  keywords: () => ["admin", "keywords"] as const,
  thresholds: () => ["admin", "panic-thresholds"] as const,
  chain: (departmentId: string | null) => ["admin", "escalation-chain", departmentId] as const,
  providerHealth: () => ["admin", "provider-health"] as const,
  overrides: () => ["admin", "overrides"] as const,
  metrics: (days: number, departmentId: string | null) =>
    ["reports", "summary", days, departmentId] as const,
  perDoctor: (days: number) => ["reports", "per-doctor", days] as const,
};

function toQueryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      // Repeated keys, which is what FastAPI's `list[str] = Query(...)` reads.
      for (const item of value) search.append(key, String(item));
    } else {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

// ── the worklist ───────────────────────────────────────────────────────

export function useWorklist(
  filters: WorklistFilters,
  cursor: string | null,
  options: { limit?: number; withTotal?: boolean } = {},
) {
  return useQuery({
    queryKey: keys5.worklist(filters, cursor),
    queryFn: () =>
      api.get<WorklistPage>(
        `/worklist${toQueryString({
          ...filters,
          cursor,
          limit: options.limit ?? 50,
          with_total: options.withTotal,
        })}`,
      ),
    refetchInterval: WORKLIST_POLL_MS,
    // Without this, every poll blanks the table for a frame and the row the
    // doctor was about to click moves. `keepPreviousData` also keeps the list
    // stable while paging.
    placeholderData: keepPreviousData,
    staleTime: 0,
  });
}

export function useCaseDetail(caseId: string) {
  return useQuery({
    queryKey: keys5.caseDetail(caseId),
    queryFn: () => api.get<CaseDetail>(`/cases/${caseId}/detail`),
    refetchInterval: WORKLIST_POLL_MS,
    staleTime: 0,
  });
}

/** The header's system-status pill. Never blocks anything. */
export function useHealth() {
  return useQuery({
    queryKey: keys5.health(),
    queryFn: () => api.get<HealthStatus>("/health"),
    refetchInterval: 60_000,
    // An unreachable API must not put a red banner over the whole app on the
    // first flake; the pill simply shows "unknown".
    retry: false,
    staleTime: 30_000,
  });
}

// ── closure ────────────────────────────────────────────────────────────

function invalidateCase(queryClient: ReturnType<typeof useQueryClient>, caseId: string) {
  void queryClient.invalidateQueries({ queryKey: keys5.caseDetail(caseId) });
  void queryClient.invalidateQueries({ queryKey: ["worklist"] });
}

export function useCloseCase(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      closure_reason: string;
      closure_note: string | null;
      duplicate_of_case_id?: string | null;
    }) => api.post<CloseCaseResult>(`/cases/${caseId}/close`, body),
    onSuccess: () => invalidateCase(queryClient, caseId),
  });
}

export function useBulkClose() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      case_ids: string[];
      closure_reason: string;
      closure_note: string | null;
    }) => api.post<{ closed: CloseCaseResult[] }>("/cases/bulk-close", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["worklist"] });
    },
  });
}

export function useReopenCase(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { reason: string }) =>
      api.post<ReopenResult>(`/cases/${caseId}/reopen`, body),
    onSuccess: () => invalidateCase(queryClient, caseId),
  });
}

export function useAddNote(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { note: string }) => api.post(`/cases/${caseId}/notes`, body),
    onSuccess: () => invalidateCase(queryClient, caseId),
  });
}

export function useReassignCase(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { new_owner_id: string; reason: string; reassigned_by: string }) =>
      api.post(`/cases/${caseId}/reassign`, body),
    onSuccess: () => invalidateCase(queryClient, caseId),
  });
}

// ── audit ──────────────────────────────────────────────────────────────

export function useAuditTrail(
  filters: Record<string, unknown>,
  afterSeq: number | null,
) {
  return useQuery({
    queryKey: keys5.audit(filters, afterSeq),
    queryFn: () =>
      api.get<AuditPage>(
        `/audit${toQueryString({ ...filters, after_seq: afterSeq, limit: 100 })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useVerifyChain(from: string | null, to: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys5.auditVerify(from, to),
    queryFn: () =>
      api.get<ChainVerification>(`/audit/verify${toQueryString({ from, to })}`),
    enabled,
    // Verification recomputes every hash. It is run on demand, never polled.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

export function useAnchors() {
  return useQuery({
    queryKey: keys5.anchors(),
    queryFn: () => api.get<AuditAnchor[]>("/audit/anchors?limit=30"),
  });
}

// ── admin ──────────────────────────────────────────────────────────────

export function useNodeBStatus() {
  return useQuery({
    queryKey: keys5.nodeB(),
    queryFn: () => api.get<NodeBStatus>("/admin/node-b"),
    refetchInterval: 30_000,
  });
}

export function useKillSwitch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { llm_enabled: boolean; reason: string }) =>
      api.post<NodeBStatus>("/admin/node-b/kill-switch", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys5.nodeB() });
      void queryClient.invalidateQueries({ queryKey: keys5.health() });
    },
  });
}

export function useAdminUsers(includeInactive = false) {
  return useQuery({
    queryKey: [...keys5.adminUsers(), includeInactive],
    queryFn: () =>
      api.get<AdminUserRow[]>(
        `/admin/users${toQueryString({ include_inactive: includeInactive })}`,
      ),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
      api.patch<AdminUserRow>(`/admin/users/${id}`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys5.adminUsers() });
    },
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post<{ id: string; employee_code: string; temporary_password: string | null }>(
        "/admin/users",
        body,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys5.adminUsers() });
    },
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: (userId: string) =>
      api.post<{ user_id: string; temporary_password: string }>(
        `/admin/users/${userId}/reset-password`,
      ),
  });
}

export function useKeywords(includeInactive = false) {
  return useQuery({
    queryKey: [...keys5.keywords(), includeInactive],
    queryFn: () =>
      api.get<KeywordRow[]>(
        `/admin/keywords${toQueryString({ include_inactive: includeInactive })}`,
      ),
  });
}

export function useUpdateKeyword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
      api.patch<KeywordRow>(`/admin/keywords/${id}`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys5.keywords() });
    },
  });
}

export function usePanicThresholds() {
  return useQuery({
    queryKey: keys5.thresholds(),
    queryFn: () => api.get<PanicThresholdRow[]>("/admin/panic-thresholds"),
  });
}

export function useEscalationChain(departmentId: string | null) {
  return useQuery({
    queryKey: keys5.chain(departmentId),
    queryFn: () =>
      api.get<EscalationRungRow[]>(
        `/admin/escalation-chain${toQueryString({ department_id: departmentId })}`,
      ),
  });
}

export function useProviderHealth() {
  return useQuery({
    queryKey: keys5.providerHealth(),
    queryFn: () => api.get<ProviderHealthRow[]>("/admin/provider-health"),
    refetchInterval: 60_000,
  });
}

export function useOverrides() {
  return useQuery({
    queryKey: keys5.overrides(),
    queryFn: () => api.get<OverrideRow[]>("/admin/overrides?limit=200"),
  });
}

// ── reports ────────────────────────────────────────────────────────────

export function useMetrics(days: number, departmentId: string | null) {
  return useQuery({
    queryKey: keys5.metrics(days, departmentId),
    queryFn: () =>
      api.get<MetricsSummary>(
        `/reports/summary${toQueryString({ days, department_id: departmentId })}`,
      ),
  });
}

export function usePerDoctor(days: number) {
  return useQuery({
    queryKey: keys5.perDoctor(days),
    queryFn: () => api.get<DoctorAckRow[]>(`/reports/per-doctor${toQueryString({ days })}`),
  });
}
