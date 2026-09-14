/**
 * Step 3 — read it back in plain language.
 *
 * "Dr Asha Menon will review Urine Culture by 14 Mar 2026, 6:00 PM." A doctor
 * confirming a discharge should not have to decode a table of ids to know what
 * they are agreeing to. This is the last point at which a wrong owner or a
 * wrong date is cheap to fix.
 */

import { Banner } from "../../components/ui/Banner";
import { Button } from "../../components/ui/Button";
import type { ApiError } from "../../api/client";
import type { BlockingOrder, ContractedOrder } from "../../api/types";
import type { Assignment } from "../../lib/draft";
import { formatDeadline, inputValueToIso } from "../../lib/datetime";

export interface Step3Props {
  orders: BlockingOrder[];
  assignments: Record<string, Assignment>;
  alreadyContracted: ContractedOrder[];
  nameFor: (doctorId: string) => string;
  onBack: () => void;
  onConfirm: () => void;
  submitting: boolean;
  /** True once contracts are written but the discharge itself failed. */
  contractsAlreadyCreated: boolean;
  error: ApiError | null;
}

export function Step3Review({
  orders,
  assignments,
  alreadyContracted,
  nameFor,
  onBack,
  onConfirm,
  submitting,
  contractsAlreadyCreated,
  error,
}: Step3Props) {
  const sentences = orders.map((order) => {
    const assignment = assignments[order.order_id];
    const iso = inputValueToIso(assignment?.expected_by_input ?? "");
    return {
      key: order.order_id,
      doctor: nameFor(assignment?.responsible_doctor_id ?? ""),
      test: order.test_name,
      when: formatDeadline(iso),
    };
  });

  return (
    <section aria-labelledby="step3-heading" className="space-y-6">
      <h2 id="step3-heading" className="text-lg font-semibold text-ink">
        Step 3 — Confirm
      </h2>

      {error ? (
        <Banner tone="danger" title={error.problem.title}>
          {error.problem.detail ? <p>{error.problem.detail}</p> : null}
          {error.violations.length > 0 ? (
            <ul className="mt-2 list-inside list-disc">
              {error.violations.map((violation, index) => (
                <li key={`${violation.code}-${index}`}>{violation.detail}</li>
              ))}
            </ul>
          ) : null}
          {contractsAlreadyCreated ? (
            <p className="mt-2 font-medium">
              The assignments were saved. Only the discharge itself did not
              complete — confirming again will not duplicate them.
            </p>
          ) : null}
        </Banner>
      ) : null}

      {sentences.length > 0 ? (
        <div className="rounded-md border border-line bg-surface p-4">
          <h3 className="text-sm font-semibold text-ink">
            These results will be tracked after discharge
          </h3>
          <ul className="mt-3 space-y-2">
            {sentences.map((sentence) => (
              <li key={sentence.key} className="text-sm leading-relaxed text-ink">
                <span className="font-medium">{sentence.doctor}</span> will review{" "}
                <span className="font-medium">{sentence.test}</span> by{" "}
                <span className="font-medium">{sentence.when}</span>.
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {alreadyContracted.length > 0 ? (
        <div className="rounded-md border border-line bg-surface p-4">
          <h3 className="text-sm font-semibold text-ink">
            Already assigned before this discharge
          </h3>
          <ul className="mt-3 space-y-2">
            {alreadyContracted.map((order) => (
              <li key={order.order_id} className="text-sm leading-relaxed text-ink">
                <span className="font-medium">
                  {nameFor(order.responsible_doctor_id)}
                </span>{" "}
                will review <span className="font-medium">{order.test_name}</span> by{" "}
                <span className="font-medium">{formatDeadline(order.expected_by)}</span>.
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {sentences.length === 0 && alreadyContracted.length === 0 ? (
        <Banner tone="info" title="Nothing outstanding">
          <p>This encounter has no investigations awaiting a result.</p>
        </Banner>
      ) : null}

      <div className="flex justify-between">
        <Button variant="secondary" onClick={onBack} disabled={submitting}>
          Back
        </Button>
        <Button onClick={onConfirm} disabled={submitting}>
          {submitting ? "Completing discharge…" : "Confirm discharge"}
        </Button>
      </div>
    </section>
  );
}
