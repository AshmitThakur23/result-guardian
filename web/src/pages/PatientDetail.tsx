/**
 * Phase 1.5 — one patient and their encounters.
 *
 * The step between finding a patient and opening the admission that actually
 * needs work. Active encounters lead, because that is the clerk's real
 * question: which admission am I here about?
 */

import { Link, useParams } from "react-router-dom";

import { usePatient } from "../api/queries";
import type { PatientEncounterRow } from "../api/types";
import { Banner } from "../components/ui/Banner";
import { Empty, ErrorState, Loading } from "../components/ui/States";
import { formatShort } from "../lib/datetime";
import { UUID_RE } from "../lib/ids";

export function PatientDetailPage() {
  const { patientId = "" } = useParams<{ patientId: string }>();

  if (!UUID_RE.test(patientId)) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Banner tone="danger" title="That is not a valid patient reference">
          <p>Check the link and try again.</p>
        </Banner>
      </main>
    );
  }
  return <Detail patientId={patientId} />;
}

function Detail({ patientId }: { patientId: string }) {
  const query = usePatient(patientId);

  if (query.isPending) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Loading label="Loading patient…" />
      </main>
    );
  }
  if (query.isError || !query.data) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <ErrorState
          error={query.error}
          onRetry={() => void query.refetch()}
          fallbackTitle="Could not load this patient"
        />
      </main>
    );
  }

  const { patient, encounters } = query.data;
  const active = encounters.filter((e) => e.status === "active");
  const past = encounters.filter((e) => e.status !== "active");

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <nav className="text-sm">
        <Link to="/patients" className="text-brand-text underline">
          ← Back to search
        </Link>
      </nav>

      <header className="rounded-md border border-line bg-surface px-4 py-3">
        <h1 className="text-xl font-semibold text-ink">{patient.name}</h1>
        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-ink-body">
          <div className="flex gap-1">
            <dt>MRN</dt>
            <dd className="font-medium text-ink">{patient.mrn}</dd>
          </div>
          {patient.sex ? (
            <div className="flex gap-1">
              <dt>Sex</dt>
              <dd className="font-medium text-ink">{patient.sex}</dd>
            </div>
          ) : null}
          {patient.dob ? (
            <div className="flex gap-1">
              <dt>DOB</dt>
              <dd className="font-medium text-ink">{patient.dob}</dd>
            </div>
          ) : null}
          {patient.phone_primary_e164 ? (
            <div className="flex gap-1">
              <dt>Phone</dt>
              <dd className="font-medium text-ink">
                {patient.phone_primary_e164}
              </dd>
            </div>
          ) : null}
        </dl>
      </header>

      {encounters.length === 0 ? (
        <Empty title="No encounters recorded for this patient">
          <p>Admissions come from the hospital system.</p>
        </Empty>
      ) : (
        <>
          <EncounterList title="Active" rows={active} />
          <EncounterList title="Past" rows={past} />
        </>
      )}
    </main>
  );
}

function EncounterList({
  title,
  rows,
}: {
  title: string;
  rows: PatientEncounterRow[];
}) {
  if (rows.length === 0) return null;
  return (
    <section aria-labelledby={`enc-${title}`}>
      <h2 id={`enc-${title}`} className="mb-2 text-sm font-semibold text-ink">
        {title} ({rows.length})
      </h2>
      <ul className="space-y-2">
        {rows.map((encounter) => (
          <li key={encounter.id}>
            <Link
              to={`/encounters/${encounter.id}`}
              className="flex items-center justify-between gap-4 rounded-md border border-line bg-surface px-4 py-3 hover:bg-surface-sunken"
            >
              <span>
                <span className="font-medium text-ink">
                  {encounter.encounter_no}
                </span>
                <span className="ml-3 text-xs uppercase text-ink-muted">
                  {encounter.type}
                </span>
                <span className="block text-xs text-ink-muted">
                  Admitted {formatShort(encounter.admitted_at)}
                  {encounter.ward ? ` · ${encounter.ward}` : ""}
                  {encounter.bed ? ` / ${encounter.bed}` : ""}
                </span>
              </span>
              <span
                className={
                  encounter.status === "active"
                    ? "shrink-0 rounded-full bg-brand-subtle px-2 py-0.5 text-xs font-medium capitalize text-brand-text"
                    : "shrink-0 rounded-full bg-surface-sunken px-2 py-0.5 text-xs font-medium capitalize text-ink-body"
                }
              >
                {encounter.status}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
