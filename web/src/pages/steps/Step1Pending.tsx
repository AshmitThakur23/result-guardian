/**
 * Step 1 — what is still outstanding.
 *
 * There is no "skip", "discharge anyway", "remind me later" or "not required"
 * control anywhere on this step, and adding one would defeat the entire
 * product. The only way past an outstanding investigation is to give it an
 * owner and a date (step 2), or to take the override path, which is a separate
 * red button, a confirmation, and a permanent audited row.
 */

import { Banner } from "../../components/ui/Banner";
import { Button } from "../../components/ui/Button";
import type { DischargeReadiness } from "../../api/types";
import { formatShort, outstandingFor } from "../../lib/datetime";

export function Step1Pending({
  readiness,
  onContinue,
}: {
  readiness: DischargeReadiness;
  onContinue: () => void;
}) {
  const blocking = readiness.blocking_orders;
  const contracted = readiness.already_contracted;

  return (
    <section aria-labelledby="step1-heading" className="space-y-6">
      <h2 id="step1-heading" className="text-lg font-semibold text-slate-900">
        Step 1 — Pending investigations
      </h2>

      {blocking.length > 0 ? (
        <Banner
          tone="danger"
          title={
            blocking.length === 1
              ? "1 investigation has no one responsible for the result"
              : `${blocking.length} investigations have no one responsible for their results`
          }
        >
          <p>
            This discharge cannot be completed until each one has a doctor and a
            date by which the result is expected. Nothing here can be dismissed.
          </p>
        </Banner>
      ) : (
        <Banner tone="success" title="Every outstanding investigation has an owner">
          <p>
            {contracted.length === 0
              ? "This encounter has no investigations still awaiting a result."
              : "Review the assignments and complete the discharge."}
          </p>
        </Banner>
      )}

      {blocking.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Outstanding investigations with no responsible doctor
            </caption>
            <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th scope="col" className="px-4 py-2">
                  Investigation
                </th>
                <th scope="col" className="px-4 py-2">
                  Category
                </th>
                <th scope="col" className="px-4 py-2">
                  Ordered
                </th>
                <th scope="col" className="px-4 py-2">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {blocking.map((order) => (
                <tr key={order.order_id}>
                  <td className="px-4 py-3">
                    <span className="font-medium text-slate-900">
                      {order.test_name}
                    </span>
                    <span className="ml-2 text-xs text-slate-500">
                      {order.test_code}
                    </span>
                  </td>
                  <td className="px-4 py-3 capitalize text-slate-700">
                    {order.category}
                  </td>
                  <td className="px-4 py-3 text-slate-700">
                    {formatShort(order.ordered_at)}
                    <span className="block text-xs text-slate-500">
                      outstanding {outstandingFor(order.ordered_at)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium capitalize text-amber-900">
                      {order.status.replace(/_/g, " ")}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {contracted.length > 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-900">
            Already assigned ({contracted.length})
          </h3>
          <ul className="mt-2 space-y-1 text-sm text-slate-700">
            {contracted.map((order) => (
              <li key={order.order_id}>
                {order.test_name} — due {formatShort(order.expected_by)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button onClick={onContinue}>
          {blocking.length > 0 ? "Assign responsibility" : "Review and discharge"}
        </Button>
      </div>
    </section>
  );
}
