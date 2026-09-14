/**
 * Phase 6 wire types — document ingestion.
 *
 * These mirror `app/routers/documents.py` and `app/routers/reports.py`. They
 * are hand-written rather than generated because the generated version would
 * carry no explanation of which fields can be null and why, and on this
 * screen almost every nullable field is nullable for a clinically meaningful
 * reason.
 */

/** The statuses in `documents.status`'s CHECK constraint, in order. */
export const DOCUMENT_STATUSES = [
  "received",
  "classified",
  "extracting",
  "extracted",
  "failed",
  "needs_review",
] as const;

export type DocumentStatus = (typeof DOCUMENT_STATUSES)[number];

/** The two a human still has to deal with. This is the review queue. */
export const UNRESOLVED_STATUSES: DocumentStatus[] = ["failed", "needs_review"];

export interface DocumentPage {
  page_no: number;
  width_pt: number | null;
  height_pt: number | null;
  /** Decided per page, not per document — 6.2. */
  is_scanned: boolean;
  text_layer: string | null;
  /**
   * `null` on a native page: there was no OCR, so there is no confidence in
   * it. Not 0, which would mean "certainly wrong".
   */
  ocr_confidence: number | null;
  /** `null` when no image was rendered — a clean native page needs none. */
  image_url: string | null;
}

export interface DocumentRow {
  id: string;
  sha256: string;
  original_filename: string | null;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  source_channel: string;
  status: DocumentStatus;
  /** What went wrong, written to be shown to a ward clerk verbatim. */
  error_text: string | null;
  received_at: string;
  attempts: number;
  order_id: string | null;
  case_id: string | null;
}

export interface DocumentDetail extends DocumentRow {
  pages: DocumentPage[];
}

export interface DocumentSpan {
  page_no: number;
  char_start: number;
  char_end: number;
  /** PDF points, origin top-left. Same space as `width_pt`/`height_pt`. */
  bbox: { x0: number; y0: number; x1: number; y1: number };
  text: string;
}

export interface UploadResult {
  document_id: string;
  sha256: string;
  /** True when this exact file was already in the system. Not an error. */
  duplicate: boolean;
  size_bytes: number;
  mime_type: string;
  /** `clean` | `skipped` | … — `skipped` means nothing looked at the file. */
  virus_scan: string;
  message: string;
}

/** 6.4's threshold, mirrored here only to label the UI consistently. */
export const OCR_CONFIDENCE_FLOOR = 0.7;
