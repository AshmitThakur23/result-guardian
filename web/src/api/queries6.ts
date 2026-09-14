/**
 * TanStack Query hooks for the Phase 6 screens.
 *
 * The review queue polls, like the worklist does, because documents arrive
 * without anyone in this tab doing anything — a lab drops a file on the
 * watched folder and it should appear. The interval is longer than the
 * worklist's 30s: a document that failed extraction is not time-critical in
 * the way an unacknowledged critical result is, and this queue is usually
 * short.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  DocumentDetail,
  DocumentRow,
  DocumentSpan,
  UploadResult,
} from "./types6";

export const REVIEW_QUEUE_POLL_MS = 60_000;

export const keys6 = {
  reviewQueue: () => ["documents", "review-queue"] as const,
  document: (id: string) => ["documents", id] as const,
  spans: (id: string, pageNo: number | null) =>
    ["documents", id, "spans", pageNo] as const,
};

export function useReviewQueue() {
  return useQuery({
    queryKey: keys6.reviewQueue(),
    queryFn: () =>
      api.get<{ documents: DocumentRow[]; count: number }>(
        "/documents/review-queue",
      ),
    refetchInterval: REVIEW_QUEUE_POLL_MS,
  });
}

export function useDocument(id: string | undefined) {
  return useQuery({
    queryKey: keys6.document(id ?? ""),
    queryFn: () => api.get<DocumentDetail>(`/documents/${id}`),
    enabled: Boolean(id),
    // A document being extracted right now changes underneath this screen.
    // Poll while it is in flight, and stop once it has settled — there is
    // nothing to watch on a document that already failed.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "received" || status === "extracting" ? 3_000 : false;
    },
  });
}

export function useDocumentSpans(id: string | undefined, pageNo: number | null) {
  return useQuery({
    queryKey: keys6.spans(id ?? "", pageNo),
    queryFn: () =>
      api.get<{ spans: DocumentSpan[] }>(
        `/documents/${id}/spans${pageNo ? `?page_no=${pageNo}` : ""}`,
      ),
    enabled: Boolean(id),
  });
}

export function useUploadReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, orderId }: { file: File; orderId?: string }) => {
      const form = new FormData();
      form.append("file", file);
      if (orderId) form.append("order_id", orderId);
      return api.postForm<UploadResult>("/reports/upload", form);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys6.reviewQueue() });
    },
  });
}

/** 6.5's admin retry button. */
export function useRetryDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<{ document_id: string; status: string }>(
        `/documents/${id}/retry`,
      ),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: keys6.document(id) });
      void queryClient.invalidateQueries({ queryKey: keys6.reviewQueue() });
    },
  });
}
