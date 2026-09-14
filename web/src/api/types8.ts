/**
 * Phase 8 types — the explanation, and the citations behind it.
 *
 * `explanation` is `string | null` and that nullability is the API's contract,
 * not an oversight. Four different things produce `null`:
 *
 *   1. no approved guidance matched — NODE B was never called
 *   2. NODE B unreachable, switched off, or too slow
 *   3. the model returned nothing usable
 *   4. **every citation failed the span verifier**
 *
 * In all four the `note` says which, and the flag underneath is untouched.
 */

export interface ExplainEvidence {
  chunk_id: string;
  /** What the model claimed the source says. Verified before it got here. */
  quoted_text: string;
  document_title: string;
  section_path: string | null;
  page_no: number | null;
  /** Offsets into `chunk_text`, so the quote can be highlighted in place. */
  char_start: number;
  char_end: number;
  /** 1.0 = copied exactly. Below that, re-typed — and the reader may want to know. */
  match_ratio: number;
  /** The surrounding passage, so a clinician reads the quote in context. */
  chunk_text: string;
}

export interface ExplainResponse {
  case_id: string;
  /** `null` whenever nothing survived verification. Never partial. */
  explanation: string | null;
  evidence: ExplainEvidence[];
  note: string;
  sources_considered: number;
  /** How many citations the verifier threw away. Shown even when 0. */
  rejected_count: number;
}

export interface KbDocument {
  id: string;
  title: string;
  publisher: string;
  doc_type: string;
  version: string;
  /** Unapproved documents are invisible to retrieval. */
  approved: boolean;
  chunks: number;
}
