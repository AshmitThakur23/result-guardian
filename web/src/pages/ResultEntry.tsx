/**
 * Phase 3.7 — manual result entry for a lab tech.
 *
 *     Numeric panel form; culture form (organism + antibiotic grid);
 *     narrative form; preview panel showing predicted severity before save.
 *
 * Three forms on one screen rather than three screens, because one report can
 * be all three at once: a urine culture with a colony count, a pus-cell
 * microscopy note, and a creatinine on the same page of the same printout.
 * Splitting them would force a tech to save three times and would give the
 * rule engine three results to reconcile.
 *
 * This is the same endpoint the Phase 6 PDF pipeline and the Phase 9 HL7 feed
 * will call. It is the permanent fallback when extraction fails, so it is
 * built as a production screen: the whole thing is keyboard-operable, every
 * field is labelled, and nothing here decides anything clinical — the server
 * classifies, this screen only reports what it would say.
 */

import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useEncounterDetail, useRecordResult } from "../api/queries";
import {
  INTERPRETATIONS,
  NARRATIVE_SECTIONS,
  REPORT_STATUSES,
} from "../api/types";
import type {
  AnalyteIn,
  Interpretation,
  NarrativeIn,
  NarrativeSection,
  OrderRow,
  OrganismIn,
  ReportStatus,
  ResultContent,
} from "../api/types";
import { ResultPreviewPanel } from "../components/ResultPreviewPanel";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { ErrorState, Loading } from "../components/ui/States";

type AnalyteRow = AnalyteIn & { key: string };
type SensitivityRow = { key: string; antibiotic_name: string; interpretation: Interpretation };
type OrganismRow = Omit<OrganismIn, "sensitivities"> & {
  key: string;
  sensitivities: SensitivityRow[];
};
type NarrativeRow = NarrativeIn & { key: string };

let counter = 0;
const nextKey = () => `row-${(counter += 1)}`;

const blankAnalyte = (): AnalyteRow => ({
  key: nextKey(),
  test_name: "",
  value_numeric: "",
  value_raw: "",
  unit: "",
  ref_low: "",
  ref_high: "",
});

const blankOrganism = (): OrganismRow => ({
  key: nextKey(),
  organism_name: "",
  colony_count: "",
  specimen_type: "",
  sensitivities: [blankSensitivity()],
});

function blankSensitivity(): SensitivityRow {
  return { key: nextKey(), antibiotic_name: "", interpretation: "S" };
}

const blankNarrative = (): NarrativeRow => ({
  key: nextKey(),
  section: "impression",
  text: "",
});

/**
 * Only rows a person has actually filled in are sent.
 *
 * A half-typed row is not a finding, and sending one would either be rejected
 * by the server or -- worse -- graded. An analyte needs a name and something
 * resembling a value; an organism needs a name; a section needs text.
 */
function toContent(
  analytes: AnalyteRow[],
  organisms: OrganismRow[],
  narratives: NarrativeRow[],
): ResultContent {
  return {
    analytes: analytes
      .filter(
        (row) =>
          row.test_name.trim() !== "" &&
          ((row.value_numeric ?? "").trim() !== "" ||
            (row.value_raw ?? "").trim() !== ""),
      )
      .map((row) => ({
        test_name: row.test_name.trim(),
        value_numeric: (row.value_numeric ?? "").trim() || null,
        value_raw: (row.value_raw ?? "").trim() || null,
        unit: (row.unit ?? "").trim() || null,
        ref_low: (row.ref_low ?? "").trim() || null,
        ref_high: (row.ref_high ?? "").trim() || null,
      })),
    organisms: organisms
      .filter((row) => row.organism_name.trim() !== "")
      .map((row) => ({
        organism_name: row.organism_name.trim(),
        colony_count: (row.colony_count ?? "").trim() || null,
        specimen_type: (row.specimen_type ?? "").trim() || null,
        sensitivities: row.sensitivities
          .filter((s) => s.antibiotic_name.trim() !== "")
          .map((s) => ({
            antibiotic_name: s.antibiotic_name.trim(),
            interpretation: s.interpretation,
          })),
      })),
    narratives: narratives
      .filter((row) => row.text.trim() !== "")
      .map((row) => ({ section: row.section, text: row.text.trim() })),
  };
}

export function ResultEntryPage() {
  const { encounterId = "", orderId = "" } = useParams();
  const navigate = useNavigate();
  const encounter = useEncounterDetail(encounterId);
  const record = useRecordResult(orderId, encounterId);

  const [reportStatus, setReportStatus] = useState<ReportStatus>("final");
  const [sourceRef, setSourceRef] = useState("");
  const [analytes, setAnalytes] = useState<AnalyteRow[]>([]);
  const [organisms, setOrganisms] = useState<OrganismRow[]>([]);
  const [narratives, setNarratives] = useState<NarrativeRow[]>([]);
  const [saved, setSaved] = useState<string | null>(null);

  const content = useMemo(
    () => toContent(analytes, organisms, narratives),
    [analytes, organisms, narratives],
  );

  const isEmpty =
    content.analytes.length === 0 &&
    content.organisms.length === 0 &&
    content.narratives.length === 0;

  if (encounter.isPending) return <Loading label="Loading the investigation…" />;
  if (encounter.isError)
    return (
      <main className="mx-auto max-w-5xl px-4 py-8">
        <ErrorState error={encounter.error} />
      </main>
    );

  const order: OrderRow | undefined = encounter.data.orders.find(
    (o) => o.id === orderId,
  );

  if (order === undefined) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Banner tone="warning" title="That investigation is not on this encounter.">
          <Link
            to={`/encounters/${encounterId}`}
            className="text-brand-text underline"
          >
            Back to the encounter
          </Link>
        </Banner>
      </main>
    );
  }

  async function save() {
    try {
      const result = await record.mutateAsync({
        report_status: reportStatus,
        source_ref: sourceRef.trim() || null,
        ...content,
      });
      setSaved(result.result_id);
    } catch {
      // Rendered from `record.error`. Swallowed only so an unhandled
      // rejection does not reach the console.
    }
  }

  if (saved !== null) {
    return (
      <main className="mx-auto max-w-5xl space-y-4 px-4 py-8">
        <Banner tone="success" title="Result recorded.">
          <p>
            The rule engine has been handed this report and will classify it.
            The severity it decides is the one of record.
          </p>
        </Banner>
        <div className="flex gap-3">
          <Button onClick={() => navigate(`/encounters/${encounterId}`)}>
            Back to the encounter
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      <nav className="mb-4 text-sm">
        <Link to={`/encounters/${encounterId}`} className="text-brand-text underline">
          ← {encounter.data.patient.name}
        </Link>
      </nav>

      <header className="mb-6">
        <h1 className="text-xl font-semibold text-ink">
          Enter a result — {order.test_name}
        </h1>
        <p className="mt-1 text-sm text-ink-body">
          {order.test_code} · {order.category} · ordered{" "}
          {new Date(order.ordered_at).toLocaleString()}
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-8">
          {record.isError ? <ErrorState error={record.error} /> : null}

          {/* ── the report itself ──────────────────────────────── */}
          <section aria-labelledby="report-heading" className="space-y-4">
            <h2 id="report-heading" className="text-lg font-semibold text-ink">
              Report
            </h2>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label
                  htmlFor="report-status"
                  className="mb-1 block text-sm font-medium text-ink-body"
                >
                  Report status
                </label>
                <select
                  id="report-status"
                  value={reportStatus}
                  onChange={(e) => setReportStatus(e.target.value as ReportStatus)}
                  className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
                >
                  {REPORT_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-ink-muted">
                  {reportStatus === "preliminary"
                    ? "A preliminary report is held, not closed — the final one is still owed."
                    : reportStatus === "amended" || reportStatus === "corrected"
                      ? "An amendment reopens a closed case and re-notifies with distinct wording."
                      : "A final report is the one the case closes on."}
                </p>
              </div>
              <div>
                <label
                  htmlFor="source-ref"
                  className="mb-1 block text-sm font-medium text-ink-body"
                >
                  Lab reference (optional)
                </label>
                <input
                  id="source-ref"
                  value={sourceRef}
                  onChange={(e) => setSourceRef(e.target.value)}
                  placeholder="ACC-100234"
                  className="w-full rounded-md border border-line px-3 py-2 text-sm"
                />
                <p className="mt-1 text-xs text-ink-muted">
                  The accession number. It is what stops the same report being
                  taken in twice.
                </p>
              </div>
            </div>
          </section>

          <NumericPanel rows={analytes} setRows={setAnalytes} />
          <CulturePanel rows={organisms} setRows={setOrganisms} />
          <NarrativePanel rows={narratives} setRows={setNarratives} />

          <div className="flex items-center gap-3 border-t border-line pt-6">
            <Button onClick={save} disabled={isEmpty || record.isPending}>
              {record.isPending ? "Saving…" : "Save result"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => navigate(`/encounters/${encounterId}`)}
            >
              Cancel
            </Button>
            {isEmpty ? (
              <p className="text-sm text-ink-body">
                Nothing to save yet — a report needs a value, an organism or a
                section.
              </p>
            ) : null}
          </div>
        </div>

        <ResultPreviewPanel orderId={orderId} content={content} />
      </div>
    </main>
  );
}

/* ── the numeric panel form ───────────────────────────────────────── */

function NumericPanel({
  rows,
  setRows,
}: {
  rows: AnalyteRow[];
  setRows: React.Dispatch<React.SetStateAction<AnalyteRow[]>>;
}) {
  const update = (key: string, patch: Partial<AnalyteRow>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  return (
    <section aria-labelledby="numeric-heading" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 id="numeric-heading" className="text-lg font-semibold text-ink">
          Numeric panel
        </h2>
        <Button
          variant="secondary"
          onClick={() => setRows((prev) => [...prev, blankAnalyte()])}
        >
          Add a test
        </Button>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-ink-body">
          No numeric values on this report.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-ink-body">
                <th scope="col" className="py-2 pr-3 font-medium">
                  Test
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  Value
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  Unit
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  Ref low
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  Ref high
                </th>
                <th scope="col" className="py-2 font-medium">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.key} className="border-b border-slate-100">
                  <td className="py-2 pr-3">
                    <Cell
                      id={`analyte-name-${row.key}`}
                      label={`Test name, row ${index + 1}`}
                      value={row.test_name}
                      onChange={(v) => update(row.key, { test_name: v })}
                      placeholder="Potassium"
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <Cell
                      id={`analyte-value-${row.key}`}
                      label={`Value, row ${index + 1}`}
                      value={row.value_numeric ?? ""}
                      onChange={(v) =>
                        // A censored value ("<0.01") is not a number, and it
                        // is not a missing value either -- on a troponin it is
                        // the worst one. It goes through as text so the rule
                        // engine can bound it rather than discard it.
                        /^-?[0-9]*\.?[0-9]*$/.test(v)
                          ? update(row.key, { value_numeric: v, value_raw: "" })
                          : update(row.key, { value_numeric: "", value_raw: v })
                      }
                      value2={row.value_raw ?? ""}
                      placeholder="4.2 or <0.01"
                      inputMode="text"
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <Cell
                      id={`analyte-unit-${row.key}`}
                      label={`Unit, row ${index + 1}`}
                      value={row.unit ?? ""}
                      onChange={(v) => update(row.key, { unit: v })}
                      placeholder="mmol/L"
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <Cell
                      id={`analyte-low-${row.key}`}
                      label={`Reference low, row ${index + 1}`}
                      value={row.ref_low ?? ""}
                      onChange={(v) => update(row.key, { ref_low: v })}
                      placeholder="3.5"
                      inputMode="decimal"
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <Cell
                      id={`analyte-high-${row.key}`}
                      label={`Reference high, row ${index + 1}`}
                      value={row.ref_high ?? ""}
                      onChange={(v) => update(row.key, { ref_high: v })}
                      placeholder="5.1"
                      inputMode="decimal"
                    />
                  </td>
                  <td className="py-2">
                    <Button
                      variant="ghost"
                      onClick={() =>
                        setRows((prev) => prev.filter((r) => r.key !== row.key))
                      }
                      aria-label={`Remove row ${index + 1}${
                        row.test_name ? `, ${row.test_name}` : ""
                      }`}
                    >
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-ink-muted">
            A value with no reference range is not treated as normal — it is
            flagged for a human to check.
          </p>
        </div>
      )}
    </section>
  );
}

/* ── the culture form ─────────────────────────────────────────────── */

function CulturePanel({
  rows,
  setRows,
}: {
  rows: OrganismRow[];
  setRows: React.Dispatch<React.SetStateAction<OrganismRow[]>>;
}) {
  const update = (key: string, patch: Partial<OrganismRow>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  return (
    <section aria-labelledby="culture-heading" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 id="culture-heading" className="text-lg font-semibold text-ink">
          Culture and sensitivity
        </h2>
        <Button
          variant="secondary"
          onClick={() => setRows((prev) => [...prev, blankOrganism()])}
        >
          Add an organism
        </Button>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-ink-body">No growth entered on this report.</p>
      ) : null}

      {rows.map((organism, index) => (
        <fieldset
          key={organism.key}
          className="space-y-4 rounded-md border border-line p-4"
        >
          <legend className="px-1 text-sm font-medium text-ink-body">
            Organism {index + 1}
          </legend>

          <div className="grid gap-4 md:grid-cols-3">
            <Cell
              id={`organism-name-${organism.key}`}
              label="Organism"
              value={organism.organism_name}
              onChange={(v) => update(organism.key, { organism_name: v })}
              placeholder="Escherichia coli, or No growth"
              block
            />
            <Cell
              id={`organism-count-${organism.key}`}
              label="Colony count"
              value={organism.colony_count ?? ""}
              onChange={(v) => update(organism.key, { colony_count: v })}
              placeholder=">100,000 CFU/mL"
              block
            />
            <Cell
              id={`organism-specimen-${organism.key}`}
              label="Specimen"
              value={organism.specimen_type ?? ""}
              onChange={(v) => update(organism.key, { specimen_type: v })}
              placeholder="urine"
              block
            />
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-medium text-ink-body">
                Sensitivity panel
              </h3>
              <Button
                variant="secondary"
                onClick={() =>
                  update(organism.key, {
                    sensitivities: [...organism.sensitivities, blankSensitivity()],
                  })
                }
              >
                Add an antibiotic
              </Button>
            </div>

            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-ink-body">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Antibiotic
                  </th>
                  {INTERPRETATIONS.map((value) => (
                    <th key={value} scope="col" className="py-2 pr-3 font-medium">
                      {value === "S"
                        ? "S — susceptible"
                        : value === "I"
                          ? "I — intermediate"
                          : "R — resistant"}
                    </th>
                  ))}
                  <th scope="col" className="py-2 font-medium">
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {organism.sensitivities.map((sensitivity, sIndex) => (
                  <tr key={sensitivity.key} className="border-b border-slate-100">
                    <td className="py-2 pr-3">
                      <Cell
                        id={`abx-${sensitivity.key}`}
                        label={`Antibiotic ${sIndex + 1} for organism ${index + 1}`}
                        value={sensitivity.antibiotic_name}
                        onChange={(v) =>
                          update(organism.key, {
                            sensitivities: organism.sensitivities.map((s) =>
                              s.key === sensitivity.key
                                ? { ...s, antibiotic_name: v }
                                : s,
                            ),
                          })
                        }
                        placeholder="Ceftriaxone"
                      />
                    </td>
                    {INTERPRETATIONS.map((value) => (
                      <td key={value} className="py-2 pr-3">
                        <label className="inline-flex items-center gap-2">
                          <input
                            type="radio"
                            // One radio group per antibiotic row: S, I and R
                            // are exclusive, and a grid of checkboxes would let
                            // a tech record an organism as both S and R.
                            name={`interpretation-${sensitivity.key}`}
                            value={value}
                            checked={sensitivity.interpretation === value}
                            onChange={() =>
                              update(organism.key, {
                                sensitivities: organism.sensitivities.map((s) =>
                                  s.key === sensitivity.key
                                    ? { ...s, interpretation: value }
                                    : s,
                                ),
                              })
                            }
                            className="h-4 w-4"
                          />
                          <span className="sr-only">
                            {value} for {sensitivity.antibiotic_name || "this antibiotic"}
                          </span>
                          <span aria-hidden="true">{value}</span>
                        </label>
                      </td>
                    ))}
                    <td className="py-2">
                      <Button
                        variant="ghost"
                        onClick={() =>
                          update(organism.key, {
                            sensitivities: organism.sensitivities.filter(
                              (s) => s.key !== sensitivity.key,
                            ),
                          })
                        }
                        aria-label={`Remove antibiotic ${sIndex + 1} from organism ${
                          index + 1
                        }`}
                      >
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Button
            variant="ghost"
            onClick={() =>
              setRows((prev) => prev.filter((r) => r.key !== organism.key))
            }
            aria-label={`Remove organism ${index + 1}`}
          >
            Remove this organism
          </Button>
        </fieldset>
      ))}
    </section>
  );
}

/* ── the narrative form ───────────────────────────────────────────── */

function NarrativePanel({
  rows,
  setRows,
}: {
  rows: NarrativeRow[];
  setRows: React.Dispatch<React.SetStateAction<NarrativeRow[]>>;
}) {
  const update = (key: string, patch: Partial<NarrativeRow>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  return (
    <section aria-labelledby="narrative-heading" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 id="narrative-heading" className="text-lg font-semibold text-ink">
          Report text
        </h2>
        <Button
          variant="secondary"
          onClick={() => setRows((prev) => [...prev, blankNarrative()])}
        >
          Add a section
        </Button>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-ink-body">No report text on this result.</p>
      ) : null}

      {rows.map((row, index) => (
        <div key={row.key} className="space-y-2 rounded-md border border-line p-4">
          <div className="flex items-end justify-between gap-4">
            <div>
              <label
                htmlFor={`narrative-section-${row.key}`}
                className="mb-1 block text-sm font-medium text-ink-body"
              >
                Section
              </label>
              <select
                id={`narrative-section-${row.key}`}
                value={row.section}
                onChange={(e) =>
                  update(row.key, { section: e.target.value as NarrativeSection })
                }
                className="rounded-md border border-line bg-surface px-3 py-2 text-sm"
              >
                {NARRATIVE_SECTIONS.map((section) => (
                  <option key={section} value={section}>
                    {section}
                  </option>
                ))}
              </select>
            </div>
            <Button
              variant="ghost"
              onClick={() => setRows((prev) => prev.filter((r) => r.key !== row.key))}
              aria-label={`Remove section ${index + 1}`}
            >
              Remove
            </Button>
          </div>

          <label
            htmlFor={`narrative-text-${row.key}`}
            className="mb-1 block text-sm font-medium text-ink-body"
          >
            Text
          </label>
          <textarea
            id={`narrative-text-${row.key}`}
            value={row.text}
            onChange={(e) => update(row.key, { text: e.target.value })}
            rows={4}
            className="w-full rounded-md border border-line px-3 py-2 text-sm"
            placeholder="No evidence of malignancy."
          />
        </div>
      ))}

      <p className="text-xs text-ink-muted">
        Report text never closes a case on its own. The most a section can be
        is a follow-up.
      </p>
    </section>
  );
}

/* ── one labelled input ───────────────────────────────────────────── */

function Cell({
  id,
  label,
  value,
  value2,
  onChange,
  placeholder,
  inputMode,
  block,
}: {
  id: string;
  label: string;
  value: string;
  /** A second source for the same box — the raw/censored form of a value. */
  value2?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  inputMode?: "text" | "decimal";
  block?: boolean;
}) {
  return (
    <div className={block ? "" : "min-w-[8rem]"}>
      <label
        htmlFor={id}
        className={block ? "mb-1 block text-sm font-medium text-ink-body" : "sr-only"}
      >
        {label}
      </label>
      <input
        id={id}
        value={value || value2 || ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        inputMode={inputMode}
        className="w-full rounded-md border border-line px-2 py-1.5 text-sm"
      />
    </div>
  );
}
