/**
 * Phase 1.4 — the discharge gate.
 *
 * Three steps: see what is outstanding, give each one an owner and a deadline,
 * read it back, confirm. The gate's whole purpose is that there is no fourth
 * option on the happy path. The override is a separate red button that opens a
 * dialog, records a typed reason, and still tracks every investigation.
 *
 * The client never decides whether a discharge may proceed. `can_discharge` is
 * derived server-side on every read, and the discharge endpoint re-derives it
 * under a row lock regardless of what this screen believes. What lives here is
 * only the workflow that makes doing the right thing the easy thing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { GateHeader, StepIndicator } from "../components/GateHeader";
import { OverrideDialog } from "../components/OverrideDialog";
import { Banner } from "../components/ui/Banner";
import { Button } from "../components/ui/Button";
import { ApiError } from "../api/client";
import {
  useCreateContracts,
  useDischarge,
  useDischargeOverride,
  useDischargeReadiness,
  useDoctorDirectory,
  useEncounter,
} from "../api/queries";
import type {
  CreatedContract,
  DischargeContractRequest,
  DischargeOverrideRequest,
  DischargeOverrideResult,
  DischargeResult,
  UserSummary,
} from "../api/types";
import { OVERRIDE_REASON_LABELS } from "../api/types";
import { inputValueToIso, isoToInputValue } from "../lib/datetime";
import { UUID_RE } from "../lib/ids";
import type { Assignment } from "../lib/draft";
import {
  clearDraft,
  emptyAssignment,
  loadDraft,
  reconcile,
  saveDraft,
} from "../lib/draft";
import { Step1Pending } from "./steps/Step1Pending";
import { Step2Assign } from "./steps/Step2Assign";
import { Step3Review } from "./steps/Step3Review";
import { SuccessScreen } from "./steps/SuccessScreen";

export function DischargeGatePage() {
  const { encounterId = "" } = useParams<{ encounterId: string }>();

  if (!UUID_RE.test(encounterId)) {
    return (
      <Shell>
        <Banner tone="danger" title="That is not a valid encounter reference">
          <p>Check the link and try again.</p>
        </Banner>
      </Shell>
    );
  }
  return <Gate encounterId={encounterId} />;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">{children}</main>
  );
}

function Gate({ encounterId }: { encounterId: string }) {
  const encounterQuery = useEncounter(encounterId);
  const readinessQuery = useDischargeReadiness(encounterId);
  const directoryQuery = useDoctorDirectory();

  const createContracts = useCreateContracts(encounterId);
  const discharge = useDischarge(encounterId);
  const override = useDischargeOverride(encounterId);

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [assignments, setAssignments] = useState<Record<string, Assignment>>({});
  const [initialised, setInitialised] = useState(false);

  // Doctors picked on this screen, so the review sentences can print a name
  // without another round trip.
  const [pickedDoctors, setPickedDoctors] = useState<Record<string, UserSummary>>({});

  // Set once the contracts have been written. Survives a failed discharge so
  // that pressing Confirm again does not try to create them a second time --
  // the second attempt would 409 on UNIQUE (order_id) and strand the doctor on
  // an error they cannot clear.
  const [createdContracts, setCreatedContracts] = useState<CreatedContract[] | null>(
    null,
  );
  const [submitError, setSubmitError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<
    | { kind: "discharge"; data: DischargeResult }
    | { kind: "override"; data: DischargeOverrideResult; reason: string }
    | null
  >(null);
  const [overrideOpen, setOverrideOpen] = useState(false);

  const encounter = encounterQuery.data;
  const readiness = readinessQuery.data;
  const blockingOrders = useMemo(
    () => readiness?.blocking_orders ?? [],
    [readiness],
  );

  // Snapshotted at confirm time. Readiness is invalidated by the mutations, so
  // by the time the success screen renders the orders have gone from the live
  // response -- the summary would otherwise lose every test name.
  const snapshot = useRef<{
    orders: typeof blockingOrders;
    contracted: NonNullable<typeof readiness>["already_contracted"];
  }>({ orders: [], contracted: [] });

  /* ---- draft restore -------------------------------------------------- */

  useEffect(() => {
    if (initialised || !readiness || !encounter) return;

    const draft = loadDraft(encounterId);
    const attendingId = encounter.attending_doctor?.id ?? null;

    const restored: Record<string, Assignment> = {};
    for (const order of readiness.blocking_orders) {
      const saved = draft?.assignments[order.order_id];
      if (saved) {
        restored[order.order_id] = saved;
        continue;
      }
      restored[order.order_id] = {
        ...emptyAssignment(),
        responsible_doctor_id: attendingId,
        // Only default the deadline when the suggested one is still in the
        // future. An order whose turnaround has already elapsed gets an empty
        // field on purpose: the honest answer is that nobody knows when it
        // will arrive, and pre-filling a plausible date would hide that.
        expected_by_input: isFuture(order.suggested_expected_by)
          ? isoToInputValue(order.suggested_expected_by)
          : "",
      };
    }

    setAssignments(reconcile(restored, readiness.blocking_orders.map((o) => o.order_id)));
    if (draft) setStep(draft.step);
    if (encounter.attending_doctor) {
      setPickedDoctors((prev) => ({
        ...prev,
        [encounter.attending_doctor!.id]: encounter.attending_doctor!,
      }));
    }
    setInitialised(true);
  }, [initialised, readiness, encounter, encounterId]);

  /* ---- draft save ----------------------------------------------------- */

  useEffect(() => {
    // Nothing to save before restore has run, and nothing worth saving once
    // the discharge is done -- that draft is cleared, not rewritten.
    if (!initialised || result) return;
    saveDraft(encounterId, step, assignments);
  }, [initialised, result, encounterId, step, assignments]);

  /* ---- names ---------------------------------------------------------- */

  /** The record behind an id, from anything this screen already knows. */
  const doctorFor = useCallback(
    (doctorId: string | null): UserSummary | null => {
      if (!doctorId) return null;
      return pickedDoctors[doctorId] ?? directoryQuery.data?.get(doctorId) ?? null;
    },
    [pickedDoctors, directoryQuery.data],
  );

  const nameFor = useCallback(
    (doctorId: string): string => {
      if (!doctorId) return "An unassigned doctor";
      const picked = pickedDoctors[doctorId];
      if (picked) return withTitle(picked.full_name);
      const known = directoryQuery.data?.get(doctorId);
      if (known) return withTitle(known.full_name);
      // Never print a raw uuid at a doctor. A name we do not have is a display
      // gap; a uuid in a clinical sentence is noise that hides the gap.
      return "The assigned doctor";
    },
    [pickedDoctors, directoryQuery.data],
  );

  /* ---- editing -------------------------------------------------------- */

  function patchAssignment(orderId: string, patch: Partial<Assignment>) {
    setAssignments((prev) => ({
      ...prev,
      [orderId]: { ...(prev[orderId] ?? emptyAssignment()), ...patch },
    }));
  }

  function applyToAll(patch: Partial<Assignment>) {
    setAssignments((prev) => {
      const next: Record<string, Assignment> = {};
      for (const [orderId, assignment] of Object.entries(prev)) {
        next[orderId] = { ...assignment, ...patch };
      }
      return next;
    });
  }

  function rememberDoctor(doctor: UserSummary | null) {
    if (doctor) setPickedDoctors((prev) => ({ ...prev, [doctor.id]: doctor }));
  }

  /* ---- confirm -------------------------------------------------------- */

  const contractPayload: DischargeContractRequest[] = useMemo(
    () =>
      blockingOrders.flatMap((order) => {
        const assignment = assignments[order.order_id];
        const iso = inputValueToIso(assignment?.expected_by_input ?? "");
        if (!assignment?.responsible_doctor_id || !iso) return [];
        return [
          {
            order_id: order.order_id,
            responsible_doctor_id: assignment.responsible_doctor_id,
            expected_by: iso,
          },
        ];
      }),
    [blockingOrders, assignments],
  );

  async function confirmDischarge() {
    setSubmitError(null);
    snapshot.current = {
      orders: blockingOrders,
      contracted: readiness?.already_contracted ?? [],
    };

    try {
      let created = createdContracts;
      if (created === null && contractPayload.length > 0) {
        const response = await createContracts.mutateAsync(contractPayload);
        created = response.created;
        setCreatedContracts(created);
      }

      const discharged = await discharge.mutateAsync();
      clearDraft(encounterId);
      setResult({ kind: "discharge", data: discharged });
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error : unknownError(error));
    }
  }

  async function submitOverride(payload: DischargeOverrideRequest) {
    setSubmitError(null);
    snapshot.current = {
      orders: blockingOrders,
      contracted: readiness?.already_contracted ?? [],
    };
    try {
      const response = await override.mutateAsync(payload);
      clearDraft(encounterId);
      setOverrideOpen(false);
      setResult({
        kind: "override",
        data: response,
        reason: OVERRIDE_REASON_LABELS[payload.reason_code],
      });
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error : unknownError(error));
    }
  }

  /* ---- render --------------------------------------------------------- */

  if (encounterQuery.isPending || readinessQuery.isPending) {
    return (
      <Shell>
        <p role="status" className="text-sm text-ink-body">
          Loading discharge details…
        </p>
      </Shell>
    );
  }

  const loadError = encounterQuery.error ?? readinessQuery.error;
  if (loadError || !encounter || !readiness) {
    const problem = loadError instanceof ApiError ? loadError : null;
    return (
      <Shell>
        <Banner
          tone="danger"
          title={problem?.problem.title ?? "Could not load this encounter"}
        >
          <p>{problem?.problem.detail ?? "Try again in a moment."}</p>
          <Button
            variant="secondary"
            className="mt-3"
            onClick={() => {
              void encounterQuery.refetch();
              void readinessQuery.refetch();
            }}
          >
            Retry
          </Button>
        </Banner>
      </Shell>
    );
  }

  if (result) {
    return (
      <Shell>
        <GateHeader encounter={encounter} />
        <SuccessScreen
          discharge={result.data}
          createdContracts={createdContracts ?? []}
          orders={snapshot.current.orders}
          alreadyContracted={snapshot.current.contracted}
          nameFor={nameFor}
          patientName={encounter.patient.name}
          overrideReason={result.kind === "override" ? result.reason : null}
        />
      </Shell>
    );
  }

  if (encounter.status === "discharged") {
    return (
      <Shell>
        <GateHeader encounter={encounter} />
        <Banner tone="info" title="This encounter has already been discharged">
          <p>
            Discharged {encounter.discharged_at ? "on " : ""}
            {encounter.discharged_at ?? ""}. Nothing further is required here.
          </p>
        </Banner>
      </Shell>
    );
  }

  const submitting =
    createContracts.isPending || discharge.isPending || override.isPending;

  return (
    <Shell>
      <GateHeader encounter={encounter} />
      <StepIndicator current={step} />

      {!readiness.gate_applies ? (
        <Banner tone="info" title="The discharge gate does not apply to this encounter">
          <p>
            Outstanding investigations are listed for information. Only
            inpatient, emergency and daycare encounters are gated.
          </p>
        </Banner>
      ) : null}

      {step === 1 ? (
        <Step1Pending
          readiness={readiness}
          onContinue={() => setStep(blockingOrders.length > 0 ? 2 : 3)}
        />
      ) : null}

      {step === 2 ? (
        <Step2Assign
          orders={blockingOrders}
          assignments={assignments}
          onChange={(orderId, patch) => patchAssignment(orderId, patch)}
          onApplyToAll={applyToAll}
          doctorFor={doctorFor}
          onDoctorResolved={rememberDoctor}
          onBack={() => setStep(1)}
          onContinue={() => setStep(3)}
        />
      ) : null}

      {step === 3 ? (
        <Step3Review
          orders={blockingOrders}
          assignments={assignments}
          alreadyContracted={readiness.already_contracted}
          nameFor={nameFor}
          onBack={() => setStep(blockingOrders.length > 0 ? 2 : 1)}
          onConfirm={() => void confirmDischarge()}
          submitting={submitting}
          contractsAlreadyCreated={createdContracts !== null}
          error={submitError}
        />
      ) : null}

      {blockingOrders.length > 0 ? (
        <div className="border-t border-line pt-6">
          <h2 className="text-sm font-semibold text-ink">
            If this discharge cannot wait
          </h2>
          <p className="mt-1 max-w-prose text-sm text-ink-body">
            Overriding the gate discharges the patient without assigning these
            results to a doctor. It is recorded permanently and every
            investigation is flagged to the unit head.
          </p>
          <Button
            variant="danger"
            className="mt-3"
            disabled={submitting}
            onClick={() => {
              setSubmitError(null);
              setOverrideOpen(true);
            }}
          >
            Override the gate
          </Button>
        </div>
      ) : null}

      <OverrideDialog
        open={overrideOpen}
        onOpenChange={(open) => {
          setOverrideOpen(open);
          if (!open) setSubmitError(null);
        }}
        blockingCount={blockingOrders.length}
        patientName={encounter.patient.name}
        onSubmit={(payload) => void submitOverride(payload)}
        submitting={override.isPending}
        error={overrideOpen ? submitError : null}
      />
    </Shell>
  );
}

function isFuture(iso: string | null): boolean {
  if (!iso) return false;
  const at = new Date(iso).getTime();
  return Number.isFinite(at) && at > Date.now();
}

function withTitle(fullName: string): string {
  return /^(dr|prof)\b/i.test(fullName.trim()) ? fullName : `Dr ${fullName}`;
}

function unknownError(error: unknown): ApiError {
  return new ApiError({
    title: "Something went wrong completing this discharge",
    detail: error instanceof Error ? error.message : String(error),
    status: 0,
  });
}
