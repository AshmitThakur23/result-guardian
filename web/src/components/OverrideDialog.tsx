/**
 * The emergency path: discharge despite the gate.
 *
 * Friction here is the feature. The button is red and separate from the
 * primary flow, the dialog states plainly what is about to happen, the reason
 * must be typed (the server enforces 20 characters after trimming), and the
 * confirm button stays disabled until every field is real. None of this stops
 * a determined doctor -- it stops an accidental one.
 *
 * `overridden_by` is collected here because authentication is Phase 5.1. Once
 * a session exists this field goes away and the identity comes from it.
 */

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";

import { DoctorSelect } from "./DoctorSelect";
import { Banner } from "./ui/Banner";
import { Button } from "./ui/Button";
import type { ApiError } from "../api/client";
import {
  MIN_OVERRIDE_REASON_CHARS,
  OVERRIDE_REASON_CODES,
  OVERRIDE_REASON_LABELS,
} from "../api/types";
import type { DischargeOverrideRequest, OverrideReasonCode } from "../api/types";

export interface OverrideDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  blockingCount: number;
  patientName: string;
  onSubmit: (payload: DischargeOverrideRequest) => void;
  submitting: boolean;
  error: ApiError | null;
}

export function OverrideDialog({
  open,
  onOpenChange,
  blockingCount,
  patientName,
  onSubmit,
  submitting,
  error,
}: OverrideDialogProps) {
  const [reasonCode, setReasonCode] = useState<OverrideReasonCode | "">("");
  const [reasonText, setReasonText] = useState("");
  const [overriddenBy, setOverriddenBy] = useState<string | null>(null);

  const trimmed = reasonText.trim().length;
  const reasonLongEnough = trimmed >= MIN_OVERRIDE_REASON_CHARS;
  const ready = reasonCode !== "" && reasonLongEnough && overriddenBy !== null;

  function submit() {
    // `ready` already establishes that reasonCode is a real code and
    // overriddenBy is set; TypeScript narrows both through it.
    if (!ready) return;
    onSubmit({
      reason_code: reasonCode,
      reason_text: reasonText,
      overridden_by: overriddenBy,
    });
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-30 bg-slate-900/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-40 w-[min(40rem,calc(100vw-2rem))] max-h-[calc(100vh-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg bg-white p-6 shadow-xl"
          aria-describedby="override-description"
        >
          <Dialog.Title className="text-lg font-semibold text-red-900">
            Discharge without assigning {blockingCount === 1 ? "this result" : "these results"}?
          </Dialog.Title>

          <Dialog.Description id="override-description" className="mt-2 text-sm text-slate-700">
            {blockingCount} investigation{blockingCount === 1 ? "" : "s"} for{" "}
            {patientName} {blockingCount === 1 ? "has" : "have"} no responsible
            doctor. Overriding records this permanently, flags every one of them
            to the unit head, and cannot be undone.
          </Dialog.Description>

          <Banner tone="warning" title="Nothing stops being tracked" className="mt-4">
            <p>
              An override is not a dismissal. Each investigation still gets a
              tracking case — it is the unit head who inherits it, not the
              patient&rsquo;s doctor.
            </p>
          </Banner>

          {error ? (
            <Banner tone="danger" title={error.problem.title} className="mt-4">
              {error.problem.detail ? <p>{error.problem.detail}</p> : null}
            </Banner>
          ) : null}

          <div className="mt-5 space-y-4">
            <div>
              <label
                htmlFor="override-reason-code"
                className="mb-1 block text-sm font-medium text-slate-700"
              >
                Reason
              </label>
              <select
                id="override-reason-code"
                value={reasonCode}
                onChange={(event) =>
                  setReasonCode(event.target.value as OverrideReasonCode | "")
                }
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                <option value="">Choose a reason…</option>
                {OVERRIDE_REASON_CODES.map((code) => (
                  <option key={code} value={code}>
                    {OVERRIDE_REASON_LABELS[code]}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="override-reason-text"
                className="mb-1 block text-sm font-medium text-slate-700"
              >
                What happened?
              </label>
              <textarea
                id="override-reason-text"
                rows={3}
                value={reasonText}
                onChange={(event) => setReasonText(event.target.value)}
                aria-describedby="override-reason-count"
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
              />
              <p
                id="override-reason-count"
                className={
                  reasonLongEnough
                    ? "mt-1 text-xs text-slate-500"
                    : "mt-1 text-xs text-red-700"
                }
              >
                {reasonLongEnough
                  ? "This will be stored on the patient record permanently."
                  : `At least ${MIN_OVERRIDE_REASON_CHARS} characters — ${trimmed} so far.`}
              </p>
            </div>

            <div>
              <DoctorSelect
                label="Overriding doctor"
                value={overriddenBy}
                onChange={(doctorId) => setOverriddenBy(doctorId)}
              />
              <p className="mt-1 text-xs text-slate-500">
                Recorded against this override. Comes from the signed-in user
                once authentication is in place.
              </p>
            </div>
          </div>

          <div className="mt-6 flex justify-between">
            <Dialog.Close asChild>
              <Button variant="secondary" disabled={submitting}>
                Go back and assign
              </Button>
            </Dialog.Close>
            <Button variant="danger" onClick={submit} disabled={!ready || submitting}>
              {submitting ? "Recording override…" : "Override and discharge"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
