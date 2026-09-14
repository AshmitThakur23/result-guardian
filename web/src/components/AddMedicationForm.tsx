/**
 * Phase 1.5 — discharge medication entry.
 *
 * *"Required for Rule B later."* Phase 3 compares a culture's sensitivity grid
 * against what the patient actually went home on, so the only job here is
 * capture. **No interaction checking, no dose validation, no formulary
 * lookup, no AI** -- those are clinical decisions this phase has no mandate to
 * make, and a half-right one would be worse than none.
 *
 * Only the drug name is required, matching the table: a clerk copying a
 * handwritten summary often has the drug and the frequency and nothing else,
 * and refusing the row would mean Rule B gets nothing rather than something.
 */

import { useState } from "react";

import { useAddMedication } from "../api/queries";
import { Button } from "./ui/Button";
import { ErrorState } from "./ui/States";

const MAX_DURATION_DAYS = 365;

interface Fields {
  drug_name: string;
  dose: string;
  route: string;
  frequency: string;
  duration_days: string;
  atc_code: string;
  is_antibiotic: boolean;
}

const EMPTY: Fields = {
  drug_name: "",
  dose: "",
  route: "",
  frequency: "",
  duration_days: "",
  atc_code: "",
  is_antibiotic: false,
};

function validate(fields: Fields): Partial<Record<keyof Fields, string>> {
  const errors: Partial<Record<keyof Fields, string>> = {};
  if (!fields.drug_name.trim()) errors.drug_name = "Required.";

  if (fields.duration_days.trim()) {
    const days = Number(fields.duration_days);
    if (!Number.isFinite(days)) {
      errors.duration_days = "Must be a number.";
    } else if (days <= 0) {
      // Mirrors ck_discharge_medications_duration_positive.
      errors.duration_days = "Must be greater than 0.";
    } else if (days > MAX_DURATION_DAYS) {
      errors.duration_days = `Must be at most ${MAX_DURATION_DAYS}.`;
    }
  }
  return errors;
}

export function AddMedicationForm({ encounterId }: { encounterId: string }) {
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [showErrors, setShowErrors] = useState(false);
  const add = useAddMedication(encounterId);

  const errors = validate(fields);
  const set = <K extends keyof Fields>(key: K, value: Fields[K]) =>
    setFields((prev) => ({ ...prev, [key]: value }));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (Object.keys(errors).length > 0) {
      setShowErrors(true);
      return;
    }
    try {
      await add.mutateAsync({
        drug_name: fields.drug_name.trim(),
        dose: fields.dose.trim() || null,
        route: fields.route.trim() || null,
        frequency: fields.frequency.trim() || null,
        duration_days: fields.duration_days.trim() || null,
        atc_code: fields.atc_code.trim() || null,
        is_antibiotic: fields.is_antibiotic,
      });
      setFields(EMPTY);
      setShowErrors(false);
    } catch {
      // Rendered from `add.error` below.
    }
  }

  const err = (key: keyof Fields) => (showErrors ? errors[key] : undefined);

  return (
    <form onSubmit={submit} className="space-y-4" aria-labelledby="add-med-heading">
      <h3 id="add-med-heading" className="text-sm font-semibold text-ink">
        Add a discharge medication
      </h3>

      {add.isError ? <ErrorState error={add.error} /> : null}

      <div className="grid gap-4 md:grid-cols-2">
        <Field
          id="med-name"
          label="Drug name"
          value={fields.drug_name}
          onChange={(v) => set("drug_name", v)}
          error={err("drug_name")}
          placeholder="Amoxicillin"
        />
        <Field
          id="med-dose"
          label="Dose"
          value={fields.dose}
          onChange={(v) => set("dose", v)}
          placeholder="500 mg"
        />
        <Field
          id="med-route"
          label="Route"
          value={fields.route}
          onChange={(v) => set("route", v)}
          placeholder="oral"
        />
        <Field
          id="med-frequency"
          label="Frequency"
          value={fields.frequency}
          onChange={(v) => set("frequency", v)}
          placeholder="TDS"
        />
        <Field
          id="med-duration"
          label="Duration (days)"
          value={fields.duration_days}
          onChange={(v) => set("duration_days", v)}
          error={err("duration_days")}
          placeholder="5"
          inputMode="decimal"
        />
        <Field
          id="med-atc"
          label="ATC code (optional)"
          value={fields.atc_code}
          onChange={(v) => set("atc_code", v)}
          placeholder="J01CA04"
        />
      </div>

      <div className="flex items-start gap-2">
        <input
          id="med-antibiotic"
          type="checkbox"
          checked={fields.is_antibiotic}
          onChange={(event) => set("is_antibiotic", event.target.checked)}
          className="mt-1"
          aria-describedby="med-antibiotic-hint"
        />
        <label htmlFor="med-antibiotic" className="text-sm text-ink">
          This is an antibiotic
          <span id="med-antibiotic-hint" className="block text-xs text-ink-muted">
            Flagging it here is what lets a resistant culture result be matched
            against it later.
          </span>
        </label>
      </div>

      <div className="flex justify-end">
        <Button type="submit" disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add medication"}
        </Button>
      </div>
    </form>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  error,
  placeholder,
  inputMode,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  placeholder?: string;
  inputMode?: "decimal" | "text";
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-ink-body">
        {label}
      </label>
      <input
        id={id}
        type="text"
        inputMode={inputMode}
        value={value}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => onChange(event.target.value)}
        className={
          error
            ? "w-full rounded-md border border-red-500 bg-critical-subtle px-3 py-2 text-sm"
            : "w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
        }
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-xs text-critical-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
