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
                    ? "rounded-full bg-blue-700 px-3 py-1 font-medium text-white"
                    : state === "done"
                      ? "rounded-full bg-blue-100 px-3 py-1 text-blue-900"
                      : "rounded-full bg-slate-200 px-3 py-1 text-slate-600"
                }
              >
                {step}. {name}
              </span>
              {step < STEPS.length ? (
                <span aria-hidden className="text-slate-400">
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
    <header className="rounded-md border border-slate-200 bg-white px-4 py-3">
      <h1 className="text-xl font-semibold text-slate-900">
        Discharge — {encounter.patient.name}
      </h1>
      <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-600">
        <div className="flex gap-1">
          <dt>MRN</dt>
          <dd className="font-medium text-slate-800">{encounter.patient.mrn}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Encounter</dt>
          <dd className="font-medium text-slate-800">{encounter.encounter_no}</dd>
        </div>
        {encounter.ward ? (
          <div className="flex gap-1">
            <dt>Ward</dt>
            <dd className="font-medium text-slate-800">
              {encounter.ward}
              {encounter.bed ? ` / ${encounter.bed}` : ""}
            </dd>
          </div>
        ) : null}
        <div className="flex gap-1">
          <dt>Admitted</dt>
          <dd className="font-medium text-slate-800">
            {formatShort(encounter.admitted_at)}
          </dd>
        </div>
        {encounter.attending_doctor ? (
          <div className="flex gap-1">
            <dt>Attending</dt>
            <dd className="font-medium text-slate-800">
              {encounter.attending_doctor.full_name}
            </dd>
          </div>
        ) : null}
      </dl>
    </header>
  );
}
