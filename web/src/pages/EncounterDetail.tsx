/**
 * Phase 1.5 — encounter detail.
 *
 * *"Orders list with status, discharge medications, contracts."* The hub
 * between finding a patient and the Phase 1.4 discharge gate: it shows what is
 * outstanding, who owns what, and links straight to the gate.
 *
 * It reports the gate's answer but never decides it. `can_discharge` here is
 * the same server-derived value the discharge action re-checks under a row
 * lock; the button below is a link, not permission.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useEncounterDetail } from "../api/queries";
import type { DischargeMedicationRow, OrderRow } from "../api/types";
import { AddMedicationForm } from "../components/AddMedicationForm";
import { AddOrderForm } from "../components/AddOrderForm";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { Empty, ErrorState, Loading } from "../components/ui/States";
import { formatShort } from "../lib/datetime";
import { UUID_RE } from "../lib/ids";

export function EncounterDetailPage() {
  const { encounterId = "" } = useParams<{ encounterId: string }>();

  if (!UUID_RE.test(encounterId)) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Banner tone="danger" title="That is not a valid encounter reference">
          <p>Check the link and try again.</p>
        </Banner>
      </main>
    );
  }
  return <Detail encounterId={encounterId} />;
}

function Detail({ encounterId }: { encounterId: string }) {
  const query = useEncounterDetail(encounterId);
  const [addingOrder, setAddingOrder] = useState(false);
  const [addingMedication, setAddingMedication] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  if (query.isPending) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Loading label="Loading encounter…" />
      </main>
    );
  }
  if (query.isError || !query.data) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <ErrorState
          error={query.error}
          onRetry={() => void query.refetch()}
          fallbackTitle="Could not load this encounter"
        />
      </main>
    );
  }

  const encounter = query.data;
  const outstanding = encounter.orders.filter((o) => o.is_outstanding);
  const resulted = encounter.orders.filter((o) => !o.is_outstanding);

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <nav className="text-sm">
        <Link to={`/patients/${encounter.patient.id}`} className="text-blue-700 underline">
          ← {encounter.patient.name}
        </Link>
      </nav>

      <header className="rounded-md border border-slate-200 bg-white px-4 py-3">
        <h1 className="text-xl font-semibold text-slate-900">
          {encounter.encounter_no}
          <span className="ml-3 text-sm font-normal uppercase text-slate-500">
            {encounter.type}
          </span>
        </h1>
        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-600">
          <div className="flex gap-1">
            <dt>Patient</dt>
            <dd className="font-medium text-slate-800">
              {encounter.patient.name} · {encounter.patient.mrn}
            </dd>
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
          <div className="flex gap-1">
            <dt>Attending</dt>
            <dd className="font-medium text-slate-800">
              {encounter.attending_doctor?.full_name ?? "Not assigned"}
            </dd>
          </div>
          <div className="flex gap-1">
            <dt>Status</dt>
            <dd className="font-medium capitalize text-slate-800">
              {encounter.status}
            </dd>
          </div>
        </dl>
      </header>

      {notice ? (
        <Banner tone="warning" title={notice}>
          <p>
            The discharge gate will now block until someone is made responsible
            for it.
          </p>
        </Banner>
      ) : null}

      <GateSummary encounter={encounter} />

      {/* ── orders ─────────────────────────────────────────────── */}
      <section aria-labelledby="orders-heading" className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 id="orders-heading" className="text-lg font-semibold text-slate-900">
            Investigations ({encounter.orders.length})
          </h2>
          {encounter.can_add_orders ? (
            <Button
              variant="secondary"
              aria-expanded={addingOrder}
              onClick={() => setAddingOrder((open) => !open)}
            >
              {addingOrder ? "Cancel" : "Add investigation"}
            </Button>
          ) : null}
        </div>

        {!encounter.can_add_orders ? (
          <p className="text-sm text-slate-600">
            This encounter is {encounter.status}. New investigations cannot be
            added to it — one ordered after discharge could not be tracked by
            the gate.
          </p>
        ) : null}

        {addingOrder && encounter.can_add_orders ? (
          <div className="rounded-md border border-slate-200 bg-white p-4">
            <AddOrderForm
              encounterId={encounterId}
              onCreated={(testName) => {
                setAddingOrder(false);
                setNotice(`${testName} added and outstanding`);
              }}
            />
          </div>
        ) : null}

        {encounter.orders.length === 0 ? (
          <Empty title="No investigations on this encounter">
            <p>Orders normally arrive from the hospital system.</p>
          </Empty>
        ) : (
          <>
            <OrderTable
              title="Outstanding"
              rows={outstanding}
              emptyLabel="Nothing is awaiting a result."
              encounterId={encounterId}
            />
            <OrderTable
              title="Resulted or closed"
              rows={resulted}
              emptyLabel={null}
              encounterId={encounterId}
            />
          </>
        )}
      </section>

      {/* ── medications ────────────────────────────────────────── */}
      <section aria-labelledby="meds-heading" className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 id="meds-heading" className="text-lg font-semibold text-slate-900">
            Discharge medications ({encounter.medications.length})
          </h2>
          <Button
            variant="secondary"
            aria-expanded={addingMedication}
            onClick={() => setAddingMedication((open) => !open)}
          >
            {addingMedication ? "Cancel" : "Add medication"}
          </Button>
        </div>

        {addingMedication ? (
          <div className="rounded-md border border-slate-200 bg-white p-4">
            <AddMedicationForm encounterId={encounterId} />
          </div>
        ) : null}

        {encounter.medications.length === 0 ? (
          <Empty title="No discharge medications recorded">
            <p>
              Recording them now is what lets a later resistant culture result
              be matched against what the patient is actually taking.
            </p>
          </Empty>
        ) : (
          <MedicationList rows={encounter.medications} />
        )}
      </section>
    </main>
  );
}

function GateSummary({
  encounter,
}: {
  encounter: ReturnType<typeof useEncounterDetail>["data"] & object;
}) {
  if (encounter.status !== "active") {
    return (
      <Banner tone="info" title={`This encounter is ${encounter.status}`}>
        <p>
          {encounter.discharged_at
            ? `Discharged ${formatShort(encounter.discharged_at)}.`
            : "No further discharge action is required."}
        </p>
      </Banner>
    );
  }

  if (!encounter.gate_applies) {
    return (
      <Banner tone="info" title="The discharge gate does not apply to this encounter">
        <p>Only inpatient, emergency and daycare encounters are gated.</p>
      </Banner>
    );
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-md border border-slate-200 bg-white px-4 py-3">
      <div>
        {encounter.can_discharge ? (
          <p className="font-semibold text-green-800">Ready to discharge</p>
        ) : (
          <p className="font-semibold text-red-800">
            Discharge blocked — {encounter.blocking_order_count}{" "}
            {encounter.blocking_order_count === 1
              ? "investigation has"
              : "investigations have"}{" "}
            no one responsible
          </p>
        )}
        <p className="mt-1 text-sm text-slate-600">
          The gate re-checks this on the server when the discharge is attempted.
        </p>
      </div>
      <Link
        to={`/encounters/${encounter.id}/discharge`}
        className="inline-flex items-center justify-center gap-2 rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800"
      >
        Open discharge gate
      </Link>
    </div>
  );
}

function OrderTable({
  title,
  rows,
  emptyLabel,
  encounterId,
}: {
  title: string;
  rows: OrderRow[];
  emptyLabel: string | null;
  encounterId: string;
}) {
  if (rows.length === 0) {
    return emptyLabel ? (
      <div>
        <h3 className="mb-1 text-sm font-semibold text-slate-900">{title}</h3>
        <p className="text-sm text-slate-600">{emptyLabel}</p>
      </div>
    ) : null;
  }

  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-slate-900">
        {title} ({rows.length})
      </h3>
      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">{title} investigations</caption>
          <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th scope="col" className="px-4 py-2">
                Investigation
              </th>
              <th scope="col" className="px-4 py-2">
                Status
              </th>
              <th scope="col" className="px-4 py-2">
                Ordered
              </th>
              <th scope="col" className="px-4 py-2">
                Responsible
              </th>
              <th scope="col" className="px-4 py-2">
                <span className="sr-only">Result</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((order) => (
              <tr key={order.id}>
                <td className="px-4 py-3">
                  <span className="font-medium text-slate-900">
                    {order.test_name}
                  </span>
                  <span className="ml-2 text-xs text-slate-500">
                    {order.test_code}
                  </span>
                  <span className="block text-xs capitalize text-slate-500">
                    {order.category}
                    {order.external_order_id ? ` · ${order.external_order_id}` : ""}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span
                    className={
                      order.is_outstanding
                        ? "rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium capitalize text-amber-900"
                        : "rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium capitalize text-slate-700"
                    }
                  >
                    {order.status.replace(/_/g, " ")}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-700">
                  {formatShort(order.ordered_at)}
                </td>
                <td className="px-4 py-3 text-slate-700">
                  {order.contract_id ? (
                    <>
                      <span className="font-medium">
                        {order.responsible_doctor_name ?? "Assigned"}
                      </span>
                      <span className="block text-xs text-slate-500">
                        due {formatShort(order.expected_by)}
                      </span>
                    </>
                  ) : order.is_outstanding ? (
                    <span className="text-red-800">No one yet</span>
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  {/* Phase 3.7. Offered on every order, not only outstanding
                      ones: an amended report arrives after the original has
                      already closed the case, and that is exactly the report
                      that must not be turned away. */}
                  <Link
                    to={`/encounters/${encounterId}/orders/${order.id}/result`}
                    className="text-blue-700 underline"
                  >
                    {order.is_outstanding ? "Enter result" : "Enter an amendment"}
                    <span className="sr-only"> for {order.test_name}</span>
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MedicationList({ rows }: { rows: DischargeMedicationRow[] }) {
  return (
    <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 bg-white">
      {rows.map((medication) => (
        <li key={medication.id} className="px-4 py-3 text-sm">
          <span className="font-medium text-slate-900">{medication.drug_name}</span>
          {medication.is_antibiotic ? (
            <span className="ml-2 rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-900">
              antibiotic
            </span>
          ) : null}
          <span className="block text-xs text-slate-500">
            {[
              medication.dose,
              medication.route,
              medication.frequency,
              medication.duration_days ? `${medication.duration_days} days` : null,
              medication.atc_code,
            ]
              .filter(Boolean)
              .join(" · ") || "No further detail recorded"}
          </span>
        </li>
      ))}
    </ul>
  );
}
