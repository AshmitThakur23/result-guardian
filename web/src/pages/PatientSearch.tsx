/**
 * Phase 1.5 — patient search.
 *
 * *"Patient search (MRN, name, phone)."* One box for all three, because a
 * ward clerk holding a file should not have to tell the system which kind of
 * string they are about to type.
 *
 * The search is debounced rather than fired per keystroke, and never fired at
 * all on an empty box: the endpoint refuses an empty `q` deliberately, and
 * asking anyway would just render a 422 at someone who has not typed yet.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { usePatientSearch } from "../api/queries";
import type { PatientSearchRow } from "../api/types";
import { Empty, ErrorState, Loading } from "../components/ui/States";
import { formatShort } from "../lib/datetime";

const DEBOUNCE_MS = 250;

export function PatientSearchPage() {
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setQuery(typed), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [typed]);

  const search = usePatientSearch(query);
  const trimmed = query.trim();

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <header>
        <h1 className="text-xl font-semibold text-ink">Find a patient</h1>
        <p className="mt-1 text-sm text-ink-body">
          Search by MRN, name or phone number.
        </p>
      </header>

      <div>
        <label htmlFor="patient-search" className="sr-only">
          Search by MRN, name or phone number
        </label>
        <input
          id="patient-search"
          type="search"
          autoFocus
          autoComplete="off"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="MRN-4410, Sunita Rao, or 98765 43210"
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
        />
      </div>

      <Results query={trimmed} state={search} />
    </main>
  );
}

function Results({
  query,
  state,
}: {
  query: string;
  state: ReturnType<typeof usePatientSearch>;
}) {
  if (!query) {
    return (
      <Empty title="Type to search">
        <p>Any part of an MRN, a name, or a phone number will do.</p>
      </Empty>
    );
  }
  if (state.isError) {
    return <ErrorState error={state.error} onRetry={() => void state.refetch()} />;
  }
  if (state.isPending) return <Loading label="Searching…" />;

  const rows = state.data ?? [];
  if (rows.length === 0) {
    return (
      <Empty title={`No patient matches “${query}”`}>
        <p>
          Check the spelling, or try the MRN. Patients are registered in the
          hospital system, not here.
        </p>
      </Empty>
    );
  }

  return (
    <>
      <p role="status" className="text-sm text-ink-body">
        {rows.length} {rows.length === 1 ? "patient" : "patients"}
        {state.isFetching ? " · updating…" : ""}
      </p>
      <ul className="space-y-2">
        {rows.map((patient) => (
          <PatientCard key={patient.id} patient={patient} />
        ))}
      </ul>
    </>
  );
}

function PatientCard({ patient }: { patient: PatientSearchRow }) {
  return (
    <li>
      <Link
        to={`/patients/${patient.id}`}
        className="flex items-center justify-between gap-4 rounded-md border border-line bg-surface px-4 py-3 hover:bg-surface-sunken"
      >
        <span>
          <span className="font-medium text-ink">{patient.name}</span>
          <span className="ml-3 text-xs text-ink-muted">{patient.mrn}</span>
          <span className="block text-xs text-ink-muted">
            {[
              patient.sex,
              patient.dob ? `DOB ${formatShort(`${patient.dob}T00:00:00Z`)}` : null,
              patient.phone_primary_e164,
            ]
              .filter(Boolean)
              .join(" · ") || "No further identifiers recorded"}
          </span>
        </span>
        {patient.active_encounter_count > 0 ? (
          <span className="shrink-0 rounded-full bg-brand-subtle px-2 py-0.5 text-xs font-medium text-brand-text">
            {patient.active_encounter_count} active
          </span>
        ) : null}
      </Link>
    </li>
  );
}
