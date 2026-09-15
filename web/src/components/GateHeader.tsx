import type { EncounterDetail } from "../api/types";
import { formatShort } from "../lib/datetime";

const STEPS = ["Pending", "Assign", "Confirm"] as const;

export function StepIndicator({ current }: { current: 1 | 2 | 3 }) {
  return (
    <nav aria-label="Discharge steps">
      <ol className="flex items-center gap-2 text-sm">
        {STEPS.map((name, index) => {
          const step = index + 1;
          const state =
            step === current ? "current" : step < current ? "done" : "upcoming";
          return (
            <li key={name} className="flex items-center gap-2">
              <span
                aria-current={state === "current" ? "step" : undefined}
                className={
                  state === "current"
                    ? "rounded-full bg-brand px-3 py-1 font-medium text-ink-inverse"
                    : state === "done"
                      ? "rounded-full bg-brand-subtle px-3 py-1 text-brand-text"
                      : "rounded-full bg-surface-hover px-3 py-1 text-ink-body"
                }
              >
                {step}. {name}
              </span>
              {step < STEPS.length ? (
                <span aria-hidden className="text-ink-muted">
                  →
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function GateHeader({ encounter }: { encounter: EncounterDetail }) {
  return (
    <header className="rounded-md border border-line bg-surface px-4 py-3">
      <h1 className="text-xl font-semibold text-ink">
        Discharge — {encounter.patient.name}
      </h1>
      <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-ink-body">
        <div className="flex gap-1">
          <dt>MRN</dt>
          <dd className="font-medium text-ink">{encounter.patient.mrn}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Encounter</dt>
          <dd className="font-medium text-ink">{encounter.encounter_no}</dd>
        </div>
        {encounter.ward ? (
          <div className="flex gap-1">
            <dt>Ward</dt>
            <dd className="font-medium text-ink">
              {encounter.ward}
              {encounter.bed ? ` / ${encounter.bed}` : ""}
            </dd>
          </div>
        ) : null}
        <div className="flex gap-1">
          <dt>Admitted</dt>
          <dd className="font-medium text-ink">
            {formatShort(encounter.admitted_at)}
          </dd>
        </div>
        {encounter.attending_doctor ? (
          <div className="flex gap-1">
            <dt>Attending</dt>
            <dd className="font-medium text-ink">
              {encounter.attending_doctor.full_name}
            </dd>
          </div>
        ) : null}
      </dl>
    </header>
  );
}
