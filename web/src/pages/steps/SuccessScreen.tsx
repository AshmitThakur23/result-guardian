/**
 * The screen after a completed discharge.
 *
 * The reference shown per investigation is the **contract id** itself. There
 * is deliberately no shortened or prettified code: `discharge_contracts` has
 * no human-readable reference column, and inventing one in the UI would print
 * an identifier that cannot be looked up anywhere in the system.
 */

import { useState } from "react";

import { Banner } from "../../components/ui/Banner";
import { Button } from "../../components/ui/Button";
import type {
  BlockingOrder,
  ContractedOrder,
  CreatedContract,
  DischargeOverrideResult,
  DischargeResult,
} from "../../api/types";
import { formatDeadline } from "../../lib/datetime";

export interface SuccessScreenProps {
  discharge: DischargeResult | DischargeOverrideResult;
  createdContracts: CreatedContract[];
  orders: BlockingOrder[];
  alreadyContracted: ContractedOrder[];
  nameFor: (doctorId: string) => string;
  patientName: string;
  /** Set when the discharge went through the override path. */
  overrideReason?: string | null;
}

function CopyableReference({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex items-center gap-2">
      <code className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-xs text-ink">
        {value}
      </code>
      <button
        type="button"
        className="text-xs text-brand-text underline"
        onClick={() => {
          // Clipboard access is denied outright on an insecure origin, which a
          // ward machine on plain HTTP over the LAN may well be. The reference
          // is on screen either way; only the shortcut is lost.
          void navigator.clipboard
            ?.writeText(value)
            .then(() => setCopied(true))
            .catch(() => setCopied(false));
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

export function SuccessScreen({
  discharge,
  createdContracts,
  orders,
  alreadyContracted,
  nameFor,
  patientName,
  overrideReason,
}: SuccessScreenProps) {
  const testNameFor = (orderId: string) =>
    orders.find((o) => o.order_id === orderId)?.test_name ??
    alreadyContracted.find((o) => o.order_id === orderId)?.test_name ??
    "Investigation";

  const isOverride = overrideReason != null;
  const overridden = isOverride
    ? (discharge as DischargeOverrideResult).overridden
    : [];

  return (
    <section aria-labelledby="success-heading" className="space-y-6">
      <Banner
        tone={isOverride ? "warning" : "success"}
        title={
          isOverride
            ? `${patientName} discharged with the gate overridden`
            : `${patientName} discharged`
        }
      >
        <p>
          Discharged at {formatDeadline(discharge.discharged_at)}.{" "}
          {discharge.opened_cases.length + overridden.length === 0
            ? "Nothing is outstanding."
            : `${discharge.opened_cases.length + overridden.length} result${
                discharge.opened_cases.length + overridden.length === 1 ? " is" : "s are"
              } now being tracked.`}
        </p>
        {isOverride ? (
          <p className="mt-1 font-medium">
            Reason recorded: {overrideReason}. Every bypassed investigation has
            been flagged to the unit head and is still tracked.
          </p>
        ) : null}
      </Banner>

      {createdContracts.length > 0 ? (
        <div className="rounded-md border border-line bg-surface p-4">
          <h2 id="success-heading" className="text-sm font-semibold text-ink">
            Contract references
          </h2>
          <p className="mt-1 text-xs text-ink-muted">
            Quote one of these when asking about a result.
          </p>
          <ul className="mt-3 space-y-3">
            {createdContracts.map((contract) => (
              <li key={contract.contract_id} className="text-sm text-ink">
                <div>
                  <span className="font-medium">
                    {testNameFor(contract.order_id)}
                  </span>{" "}
                  — {nameFor(contract.responsible_doctor_id)}, due{" "}
                  {formatDeadline(contract.expected_by)}
                </div>
                <div className="mt-1">
                  <CopyableReference value={contract.contract_id} />
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <h2 id="success-heading" className="sr-only">
          Discharge complete
        </h2>
      )}

      {overridden.length > 0 ? (
        <div className="rounded-md border border-followup-line bg-followup-subtle p-4">
          <h3 className="text-sm font-semibold text-followup-text">
            Bypassed and flagged to the unit head
          </h3>
          <ul className="mt-2 space-y-1 text-sm text-followup-text">
            {overridden.map((record) => (
              <li key={record.override_id}>
                {testNameFor(record.order_id)} — now owned by{" "}
                {nameFor(record.flagged_owner_id)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button variant="secondary" onClick={() => window.print()}>
          Print this summary
        </Button>
      </div>
    </section>
  );
}
