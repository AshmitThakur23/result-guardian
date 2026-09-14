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

/**
 * Approved guidance that was found, with **nothing generated attached**.
 *
 * A separate type from `ExplainEvidence` on purpose: that one carries a quote a
 * model produced and the verifier confirmed, and this one has been near no
 * model at all. One list holding both would be the exact conflation the span
 * verifier exists to prevent.
 */
export interface RetrievedPassage {
  chunk_id: string;
  document_title: string;
  section_path: string | null;
  page_no: number | null;
  chunk_text: string;
}

export interface ExplainResponse {
  case_id: string;
  /** `null` whenever nothing survived verification. Never partial. */
  explanation: string | null;
  evidence: ExplainEvidence[];
  note: string;
  /**
   * The guidance itself, shown when no explanation is. A clinician who asked
   * "why does this matter?" while NODE B is off should still get the hospital's
   * own approved text, rather than an apology — the answer is usually in it.
   * Empty on success, where `evidence` already carries the passages in context.
   */
  retrieved: RetrievedPassage[];
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

/**
 * The closed vocabularies `kb_documents` enforces with a CHECK. Kept here so
 * the form offers exactly what the database accepts — a free-text box would
 * turn a typo into a 422 the user has to guess their way out of.
 */
export const KB_PUBLISHERS = ["hospital", "who", "icmr", "nlem", "other"] as const;
export const KB_DOC_TYPES = [
  "antibiotic_policy",
  "guideline",
  "antibiogram",
  "protocol",
  "sop",
  "formulary",
] as const;

export interface IngestResult {
  document_id: string;
  chunks: number;
  approved: boolean;
  /**
   * Anything in the text that looked like it belonged to a person. Returned
   * rather than blocking: a guideline may legitimately say "Patient:" in a
   * worked example, so the judgement belongs to whoever approves it.
   */
  identifier_warnings: { kind: string; excerpt: string }[];
  message: string;
}
