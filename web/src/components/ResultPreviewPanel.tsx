/**
 * Phase 3.7 — *"preview panel showing predicted severity before save"*.
 *
 * The point is not decoration. A lab tech typing a culture should see that
 * the organism is resistant to what the patient went home on **while the
 * report is still being typed**, for two reasons: it catches a transposed
 * S/R while the correction is still cheap, and a severity that appears out of
 * nowhere after saving teaches nobody anything about why.
 *
 * Everything here is labelled as a **prediction**. Nothing is written to
 * produce it, and the decision of record is made on save — so the panel says
 * "would", never "is".
 */

import { useResultPreview } from "../api/queries";
import type { ResultContent, RulePreviewRow } from "../api/types";
import { SeverityBadge } from "./SeverityBadge";
import { Banner } from "./ui/Banner";
import { ErrorState } from "./ui/States";

/** Reason codes are the engine's vocabulary, not a tech's. */
const REASONS: Record<string, string> = {
  NUM_NO_NUMERIC_VALUE: "No comparable number — not classifiable",
  NUM_NO_REFERENCE_RANGE: "No reference range — not classifiable",
  NUM_WITHIN_REFERENCE_RANGE: "Within the reference range",
  NUM_ABOVE_CRITICAL_HIGH: "At or above the critical high threshold",
  NUM_BELOW_CRITICAL_LOW: "At or below the critical low threshold",
  NUM_FAR_ABOVE_REFERENCE_RANGE: "Far above the reference range",
  NUM_FAR_BELOW_REFERENCE_RANGE: "Far below the reference range",
  NUM_ABOVE_REFERENCE_RANGE: "Above the reference range",
  NUM_BELOW_REFERENCE_RANGE: "Below the reference range",
  NUM_CENSORED_NOT_COMPARABLE: "A censored value cannot be placed in the range",
  CULT_NO_GROWTH: "No growth",
  CULT_LIKELY_CONTAMINANT: "Looks like contamination — a human should confirm",
  CULT_RESISTANT_TO_DISCHARGE_DRUG: "Resistant to a discharge antibiotic",
  CULT_INTERMEDIATE_TO_DISCHARGE_DRUG: "Intermediate to a discharge antibiotic",
  CULT_NO_DISCHARGE_ANTIBIOTIC: "Growth, and no antibiotic was prescribed",
  CULT_COVERED_BY_DISCHARGE_DRUGS: "Every discharge antibiotic is susceptible",
  CULT_MULTI_DRUG_RESISTANT_ORGANISM: "Multi-drug-resistant organism",
  CULT_NO_SENSITIVITY_DATA: "Growth, and nothing was tested against it",
  CULT_DISCHARGE_DRUG_NOT_ON_PANEL: "A discharge antibiotic was not tested",
  NARR_KEYWORD_MATCH: "A clinical finding was named",
  NARR_ALL_HITS_NEGATED: "Every finding named was negated",
  NARR_NO_KEYWORD_MATCH: "No clinical finding was named",
  NARR_HEDGED_FINDING: "A finding was hedged, not excluded",
  NARR_NO_TEXT: "No text",
  ORCH_NO_CLASSIFIABLE_CONTENT: "Nothing on this report can be classified",
};

const RULE_NAMES: Record<string, string> = {
  A_numeric: "Numeric",
  B_culture: "Culture",
  C_narrative: "Narrative",
  orchestrator: "Report",
};

function explain(row: RulePreviewRow): string {
  // The raw code is shown when it is not yet translated: an untranslated code
  // is still information, and hiding it would leave a blank row.
  return REASONS[row.reason_code] ?? row.reason_code;
}

export function ResultPreviewPanel({
  orderId,
  content,
}: {
  orderId: string;
  content: ResultContent;
}) {
  const preview = useResultPreview(orderId, content);

  const isEmpty =
    content.analytes.length === 0 &&
    content.organisms.length === 0 &&
    content.narratives.length === 0;

  return (
    <aside
      aria-labelledby="preview-heading"
      className="space-y-3 rounded-md border border-slate-300 bg-slate-50 p-4"
    >
      <div>
        <h2 id="preview-heading" className="text-sm font-semibold text-slate-900">
          Predicted severity
        </h2>
        <p className="mt-1 text-xs text-slate-600">
          What the rule engine would decide about this report as typed. Nothing
          is saved until you press Save.
        </p>
      </div>

      {isEmpty ? (
        <p className="text-sm text-slate-600">
          Enter a value, an organism or a report section to see what it would
          mean.
        </p>
      ) : preview.isError ? (
        <ErrorState error={preview.error} />
      ) : preview.data === undefined ? (
        <p className="text-sm text-slate-600" role="status">
          Grading…
        </p>
      ) : (
        <div
          // `status`, not `alert`: the panel updates on every keystroke, and an
          // assertive region would interrupt a screen-reader user mid-word.
          role="status"
          aria-live="polite"
          className="space-y-3"
        >
          <div className="flex items-center gap-3">
            <SeverityBadge severity={preview.data.severity} />
            {preview.isFetching ? (
              <span className="text-xs text-slate-500">updating…</span>
            ) : null}
          </div>

          {preview.data.severity === "critical" ? (
            <Banner tone="danger" title="This would be flagged critical.">
              The responsible doctor is notified and the case stays open until
              somebody acknowledges it.
            </Banner>
          ) : null}

          <ul className="space-y-2">
            {preview.data.rules.map((row, index) => (
              <li
                key={`${row.rule_id}-${row.subject ?? index}`}
                className="rounded border border-slate-200 bg-white px-3 py-2 text-sm"
              >
                <p className="font-medium text-slate-900">
                  {row.subject ?? RULE_NAMES[row.rule_id] ?? row.rule_id}
                </p>
                <p className="text-slate-700">{explain(row)}</p>
                {row.offending_drug ? (
                  <p className="mt-1 text-slate-700">
                    Prescribed at discharge:{" "}
                    <strong className="font-semibold">{row.offending_drug}</strong>
                  </p>
                ) : null}
                {row.alternatives_available.length > 0 ? (
                  <p className="mt-1 text-slate-600">
                    Susceptible on this panel: {row.alternatives_available.join(", ")}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>

          <dl className="space-y-1 text-xs text-slate-600">
            <div className="flex gap-2">
              <dt className="font-medium">Discharge antibiotics compared:</dt>
              <dd>
                {preview.data.discharge_antibiotics.length > 0
                  ? preview.data.discharge_antibiotics.join(", ")
                  : "none recorded for this encounter"}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Would close the case:</dt>
              <dd>{preview.data.would_auto_close ? "yes" : "no"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Rule engine:</dt>
              <dd>v{preview.data.engine_version}</dd>
            </div>
          </dl>
        </div>
      )}
    </aside>
  );
}
