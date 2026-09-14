/**
 * Step 2 — give every outstanding investigation an owner and a deadline.
 *
 * Defaults do the work: the responsible doctor starts as the encounter's
 * attending doctor, and the expected-by starts at `ordered_at + TAT` as
 * computed by the server. A doctor discharging a straightforward patient
 * should be able to reach step 3 by pressing Continue.
 */

import { useMemo, useState } from "react";

import { DoctorSelect } from "../../components/DoctorSelect";
import { Banner } from "../../components/ui/Banner";
import { Button } from "../../components/ui/Button";
import type { BlockingOrder, UserSummary } from "../../api/types";
import type { Assignment } from "../../lib/draft";
import {
  EXPECTED_BY_MESSAGES,
  checkExpectedBy,
  formatShort,
  maxExpectedByInputValue,
  minExpectedByInputValue,
} from "../../lib/datetime";

export interface Step2Props {
  orders: BlockingOrder[];
  assignments: Record<string, Assignment>;
  onChange: (orderId: string, patch: Partial<Assignment>) => void;
  onApplyToAll: (patch: Partial<Assignment>) => void;
  /**
   * Every doctor the page can already name -- the attending doctor, anyone
   * picked on this screen, and the directory. Used to label a field whose
   * selection is not in the current page of search results.
   */
  doctorFor: (doctorId: string | null) => UserSummary | null;
  /** Lets the page remember a chosen doctor so step 3 can print their name. */
  onDoctorResolved: (doctor: UserSummary | null) => void;
  onBack: () => void;
  onContinue: () => void;
}

/** True when the server had no suggestion we can honestly pre-fill. */
function needsManualDate(order: BlockingOrder): boolean {
  if (!order.suggested_expected_by) return true;
  const at = new Date(order.suggested_expected_by).getTime();
  return !Number.isFinite(at) || at <= Date.now();
}

export function Step2Assign({
  orders,
  assignments,
  onChange,
  onApplyToAll,
  doctorFor,
  onDoctorResolved,
  onBack,
  onContinue,
}: Step2Props) {
  // Frozen at mount. If `new Date()` were read on every render, the "at least
  // 1 hour from now" boundary would creep forward while the doctor is typing
  // and a field valid a second ago would go red under them.
  const [now] = useState(() => new Date());
  const [showErrors, setShowErrors] = useState(false);
  const minValue = useMemo(() => minExpectedByInputValue(now), [now]);
  const maxValue = useMemo(() => maxExpectedByInputValue(now), [now]);

  const problems = useMemo(() => {
    const found: Record<string, { doctor: boolean; expectedBy: string | null }> = {};
    for (const order of orders) {
      const assignment = assignments[order.order_id];
      const expectedByProblem = checkExpectedBy(assignment?.expected_by_input ?? "", now);
      found[order.order_id] = {
        doctor: !assignment?.responsible_doctor_id,
        expectedBy: expectedByProblem ? EXPECTED_BY_MESSAGES[expectedByProblem] : null,
      };
    }
    return found;
  }, [orders, assignments, now]);

  const incomplete = orders.filter(
    (o) => problems[o.order_id].doctor || problems[o.order_id].expectedBy,
  );

  function handleContinue() {
    if (incomplete.length > 0) {
      setShowErrors(true);
      // Move the doctor to the first field that needs them rather than making
      // them hunt down the row.
      const first = incomplete[0];
      document.getElementById(`row-${first.order_id}`)?.scrollIntoView({
        block: "center",
      });
      return;
    }
    onContinue();
  }

  const firstAssignment = orders.length > 0 ? assignments[orders[0].order_id] : undefined;

  return (
    <section aria-labelledby="step2-heading" className="space-y-6">
      <h2 id="step2-heading" className="text-lg font-semibold text-ink">
        Step 2 — Who is responsible, and by when?
      </h2>

      <p className="text-sm text-ink-body">
        Each investigation needs a doctor who will review the result and a date
        by which it is expected. Defaults come from the attending doctor and the
        test&rsquo;s turnaround time — change any that are wrong.
      </p>

      {showErrors && incomplete.length > 0 ? (
        <Banner
          tone="warning"
          title={`${incomplete.length} ${incomplete.length === 1 ? "row is" : "rows are"} incomplete`}
        >
          <p>Every row needs a responsible doctor and a valid expected-by date.</p>
        </Banner>
      ) : null}

      {orders.length > 1 && firstAssignment ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-surface px-4 py-3">
          <span className="text-sm text-ink-body">
            Same doctor and date for all {orders.length} investigations?
          </span>
          <Button
            variant="secondary"
            onClick={() =>
              onApplyToAll({
                responsible_doctor_id: firstAssignment.responsible_doctor_id,
                expected_by_input: firstAssignment.expected_by_input,
              })
            }
            disabled={!firstAssignment.responsible_doctor_id}
          >
            Apply first row to all
          </Button>
        </div>
      ) : null}

      <ul className="space-y-4">
        {orders.map((order) => {
          const assignment = assignments[order.order_id];
          const problem = problems[order.order_id];
          const errorId = `err-${order.order_id}`;
          const showDoctorError = showErrors && problem.doctor;
          const showDateError = showErrors && problem.expectedBy !== null;

          return (
            <li
              key={order.order_id}
              id={`row-${order.order_id}`}
              className="rounded-md border border-line bg-surface p-4"
            >
              <div className="mb-3">
                <span className="font-medium text-ink">{order.test_name}</span>
                <span className="ml-2 text-xs text-ink-muted">{order.test_code}</span>
                <span className="ml-3 text-xs text-ink-muted">
                  ordered {formatShort(order.ordered_at)}
                  {order.expected_tat_hours
                    ? ` · turnaround ${Number(order.expected_tat_hours)} h`
                    : " · no turnaround time recorded"}
                </span>
                {needsManualDate(order) ? (
                  <p className="mt-1 text-xs text-followup-text">
                    No usable default — this test&rsquo;s expected turnaround has
                    already passed or was never recorded. Choose a date.
                  </p>
                ) : null}
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <DoctorSelect
                    label={`Responsible doctor for ${order.test_name}`}
                    hideLabel
                    value={assignment?.responsible_doctor_id ?? null}
                    // Was: only supplied when the selection happened to be the
                    // attending doctor. Every other doctor then depended on
                    // being inside the current search page, and a restored
                    // draft naming someone further down the alphabet rendered
                    // an empty field that the gate nonetheless accepted.
                    selectedHint={doctorFor(
                      assignment?.responsible_doctor_id ?? null,
                    )}
                    onChange={(doctorId, doctor) => {
                      onChange(order.order_id, { responsible_doctor_id: doctorId });
                      onDoctorResolved(doctor);
                    }}
                    invalid={showDoctorError}
                    describedBy={showDoctorError ? errorId : undefined}
                  />
                  <p className="mt-1 text-xs text-ink-muted">Responsible doctor</p>
                  {showDoctorError ? (
                    <p id={errorId} className="mt-1 text-xs text-critical-text">
                      Choose the doctor who will review this result.
                    </p>
                  ) : null}
                </div>

                <div>
                  <label
                    htmlFor={`expected-${order.order_id}`}
                    className="sr-only"
                  >{`Result expected by, for ${order.test_name}`}</label>
                  <input
                    id={`expected-${order.order_id}`}
                    type="datetime-local"
                    value={assignment?.expected_by_input ?? ""}
                    min={minValue}
                    max={maxValue}
                    aria-invalid={showDateError || undefined}
                    aria-describedby={showDateError ? `${errorId}-date` : undefined}
                    onChange={(event) =>
                      onChange(order.order_id, {
                        expected_by_input: event.target.value,
                      })
                    }
                    className={
                      showDateError
                        ? "w-full rounded-md border border-red-500 bg-critical-subtle px-3 py-2 text-sm"
                        : "w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
                    }
                  />
                  <p className="mt-1 text-xs text-ink-muted">Result expected by</p>
                  {showDateError ? (
                    <p id={`${errorId}-date`} className="mt-1 text-xs text-critical-text">
                      {problem.expectedBy}
                    </p>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="flex justify-between">
        <Button variant="secondary" onClick={onBack}>
          Back
        </Button>
        <Button onClick={handleContinue}>Review</Button>
      </div>
    </section>
  );
}
