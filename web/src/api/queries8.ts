/**
 * Phase 8 hooks.
 *
 * `useExplain` is a **mutation, not a query**, and that is deliberate. A query
 * would run on mount, refetch on window focus, and retry on failure — each of
 * which costs ~14 s of NODE B's GPU. An explanation is something a clinician
 * *asks for*, once, and the hook should not be able to ask on their behalf.
 *
 * `retry: false` for the same reason. A failed explanation is a
 * non-event — the note says why and the flag is untouched — so retrying
 * silently would spend another 14 s to tell the reader the same thing.
 */

import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { ExplainResponse, KbDocument } from "./types8";

export const keys8 = {
  kbDocuments: () => ["kb", "documents"] as const,
};

export function useExplain(caseId: string) {
  return useMutation({
    mutationFn: (query: string) =>
      api.post<ExplainResponse>(`/cases/${caseId}/explain`, { query }),
    retry: false,
  });
}

export function useKbDocuments() {
  return useQuery({
    queryKey: keys8.kbDocuments(),
    queryFn: () => api.get<{ documents: KbDocument[]; count: number }>("/kb/documents"),
  });
}
