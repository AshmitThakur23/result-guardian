/**
 * Phase 1.5 — manual order entry.
 *
 * A stand-in for the Phase 9 HIS feed. It validates what the schema
 * constrains and invents no clinical rules: which test is appropriate for
 * which patient is the doctor's decision and stays the doctor's decision.
 *
 * The form is **not authoritative**. It is hidden once the encounter is no
 * longer active, but that is a courtesy, not a control -- the server takes a
 * row lock on the encounter and refuses the insert regardless of what this
 * component believes. See `api/app/services/orders.py`.
 */

import { useState } from "react";

import { useCreateOrder } from "../api/queries";
import { ORDER_CATEGORIES, MANUAL_ORDER_STATUSES } from "../api/types";
import type { OrderCategory, ManualOrderStatus } from "../api/types";
import { Banner } from "./ui/Banner";
import { Button } from "./ui/Button";
import { ErrorState } from "./ui/States";

const MAX_TAT_HOURS = 720;

interface Fields {
  test_code: string;
  test_name: string;
  category: OrderCategory;
  status: ManualOrderStatus;
  expected_tat_hours: string;
  external_order_id: string;
}

const EMPTY: Fields = {
  test_code: "",
  test_name: "",
  category: "lab",
  status: "ordered",
  expected_tat_hours: "",
  external_order_id: "",
};

/** Client-side mirror of the server's rules, so a doctor is told at the field
 *  rather than after submitting. The server remains the authority. */
function validate(fields: Fields): Partial<Record<keyof Fields, string>> {
  const errors: Partial<Record<keyof Fields, string>> = {};
  if (!fields.test_code.trim()) errors.test_code = "Required.";
  if (!fields.test_name.trim()) errors.test_name = "Required.";

  if (fields.expected_tat_hours.trim()) {
    const hours = Number(fields.expected_tat_hours);
    if (!Number.isFinite(hours)) {
      errors.expected_tat_hours = "Must be a number.";
    } else if (hours <= 0) {
      errors.expected_tat_hours = "Must be greater than 0.";
    } else if (hours > MAX_TAT_HOURS) {
      errors.expected_tat_hours = `Must be at most ${MAX_TAT_HOURS}.`;
    }
  }
  return errors;
}

export function AddOrderForm({
  encounterId,
  onCreated,
}: {
  encounterId: string;
  onCreated?: (testName: string, blocking: number) => void;
}) {
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [showErrors, setShowErrors] = useState(false);
  const create = useCreateOrder(encounterId);

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
      const created = await create.mutateAsync({
        test_code: fields.test_code.trim(),
        test_name: fields.test_name.trim(),
        category: fields.category,
        status: fields.status,
        expected_tat_hours: fields.expected_tat_hours.trim() || null,
        external_order_id: fields.external_order_id.trim() || null,
      });
      setFields(EMPTY);
      setShowErrors(false);
      onCreated?.(created.order.test_name, created.blocking_order_count);
    } catch {
      // Rendered from `create.error` below. Swallowed here only so an
      // unhandled rejection does not reach the console.
    }
  }

  const err = (key: keyof Fields) => (showErrors ? errors[key] : undefined);

  return (
    <form onSubmit={submit} className="space-y-4" aria-labelledby="add-order-heading">
      <h3 id="add-order-heading" className="text-sm font-semibold text-ink">
        Add an investigation
      </h3>

      {create.isError ? <ErrorState error={create.error} /> : null}

      <div className="grid gap-4 md:grid-cols-2">
        <Field
          id="order-test-code"
          label="Test code"
          value={fields.test_code}
          onChange={(v) => set("test_code", v)}
          error={err("test_code")}
          placeholder="URC"
        />
        <Field
          id="order-test-name"
          label="Test name"
          value={fields.test_name}
          onChange={(v) => set("test_name", v)}
          error={err("test_name")}
          placeholder="Urine Culture"
        />

        <div>
          <label
            htmlFor="order-category"
            className="mb-1 block text-sm font-medium text-ink-body"
          >
            Category
          </label>
          <select
            id="order-category"
            value={fields.category}
            onChange={(e) => set("category", e.target.value as OrderCategory)}
            className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
          >
            {ORDER_CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="order-status"
            className="mb-1 block text-sm font-medium text-ink-body"
          >
            Status
          </label>
          <select
            id="order-status"
            value={fields.status}
            onChange={(e) => set("status", e.target.value as ManualOrderStatus)}
            className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
          >
            {MANUAL_ORDER_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-ink-muted">
            A result is recorded by the lab, not entered here.
          </p>
        </div>

        <Field
          id="order-tat"
          label="Turnaround time (hours)"
          value={fields.expected_tat_hours}
          onChange={(v) => set("expected_tat_hours", v)}
          error={err("expected_tat_hours")}
          placeholder="48"
          inputMode="decimal"
          hint="The gate pre-fills its expected-by from this. Leave blank if unknown."
        />
        <Field
          id="order-external-id"
          label="Lab accession number (optional)"
          value={fields.external_order_id}
          onChange={(v) => set("external_order_id", v)}
          placeholder="ACC-100234"
        />
      </div>

      <Banner tone="info" title="This will block discharge">
        <p>
          A new investigation is outstanding until it is resulted. The encounter
          cannot be discharged until someone is made responsible for it.
        </p>
      </Banner>

      <div className="flex justify-end">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Adding…" : "Add investigation"}
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
  hint,
  inputMode,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  placeholder?: string;
  hint?: string;
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
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
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
      ) : hint ? (
        <p id={`${id}-hint`} className="mt-1 text-xs text-ink-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
