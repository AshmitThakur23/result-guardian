/**
 * Searchable responsible-doctor field.
 *
 * Built as an ARIA 1.2 combobox rather than a native `<select>` because the
 * list is a live server search, and rather than a library because the gate has
 * to be fully keyboard-operable and that is easier to guarantee in code we can
 * read than through a dependency.
 *
 * **No availability badge.** Whether a doctor is on duty comes from
 * `duty_roster` / `user_absences`, which are Phase 4.1 and do not exist. The
 * schema's only liveness signal is `is_active`, and inactive users are already
 * filtered out server-side, so this field shows no availability at all rather
 * than implying it knows something it does not.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { useDoctorSearch } from "../api/queries";
import type { UserSummary } from "../api/types";
import { cn } from "../lib/cn";

export interface DoctorSelectProps {
  value: string | null;
  onChange: (doctorId: string | null, doctor: UserSummary | null) => void;
  /** Rendered while closed when `value` is set but not in the current page. */
  selectedHint?: UserSummary | null;
  label: string;
  /** Visually hidden label, for the per-row fields inside a table. */
  hideLabel?: boolean;
  invalid?: boolean;
  describedBy?: string;
  disabled?: boolean;
}

export function DoctorSelect({
  value,
  onChange,
  selectedHint,
  label,
  hideLabel = false,
  invalid = false,
  describedBy,
  disabled = false,
}: DoctorSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [resolved, setResolved] = useState<UserSummary | null>(selectedHint ?? null);

  const listboxId = useId();
  const labelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data, isFetching } = useDoctorSearch(open ? query : "");
  const options = useMemo(() => data ?? [], [data]);

  useEffect(() => {
    if (selectedHint && selectedHint.id === value) setResolved(selectedHint);
  }, [selectedHint, value]);

  // A doctor chosen earlier (or restored from a draft) may not be in the
  // current search page. Keep the resolved record so the field can still print
  // a name instead of a bare uuid.
  useEffect(() => {
    if (value === null) {
      setResolved(null);
      return;
    }
    const match = options.find((o) => o.id === value);
    if (match) setResolved(match);
  }, [value, options]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => setActiveIndex(0), [query, open]);

  function choose(doctor: UserSummary) {
    setResolved(doctor);
    onChange(doctor.id, doctor);
    setOpen(false);
    setQuery("");
    inputRef.current?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        setActiveIndex((i) => (options.length === 0 ? 0 : (i + 1) % options.length));
        return;
      case "ArrowUp":
        event.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        setActiveIndex((i) =>
          options.length === 0 ? 0 : (i - 1 + options.length) % options.length,
        );
        return;
      case "Enter":
        // Only swallow Enter when it is actually choosing something. Otherwise
        // it must keep bubbling, or a keyboard user cannot submit the step.
        if (open && options[activeIndex]) {
          event.preventDefault();
          choose(options[activeIndex]);
        }
        return;
      case "Escape":
        if (open) {
          event.stopPropagation();
          setOpen(false);
          setQuery("");
        }
        return;
      case "Tab":
        setOpen(false);
        return;
      default:
    }
  }

  // A selection the field cannot name must still read as a selection. The
  // lookup above covers everyone the page knows, but a hospital larger than
  // the directory's page could still hand us an id with no record attached --
  // and rendering "" there produced a field that read as unassigned while the
  // form held a doctor and let the gate proceed. A uuid is not an option
  // either; it is noise that hides the gap rather than naming it.
  // A by-ids lookup is Phase 5.4.
  const selectedLabel =
    resolved?.full_name ?? (value ? "Doctor selected (name unavailable)" : "");
  const displayValue = open ? query : selectedLabel;

  return (
    <div ref={rootRef} className="relative">
      <label
        id={labelId}
        htmlFor={`${listboxId}-input`}
        className={cn(
          "mb-1 block text-sm font-medium text-slate-700",
          hideLabel && "sr-only",
        )}
      >
        {label}
      </label>

      <input
        id={`${listboxId}-input`}
        ref={inputRef}
        role="combobox"
        type="text"
        autoComplete="off"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-labelledby={labelId}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        aria-activedescendant={
          open && options[activeIndex] ? `${listboxId}-opt-${activeIndex}` : undefined
        }
        disabled={disabled}
        value={displayValue}
        placeholder={selectedLabel ? undefined : "Search by name or employee code"}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        className={cn(
          "w-full rounded-md border px-3 py-2 text-sm",
          "disabled:bg-slate-100 disabled:text-slate-500",
          invalid ? "border-red-500 bg-red-50" : "border-slate-300 bg-white",
        )}
      />

      {open ? (
        <ul
          id={listboxId}
          role="listbox"
          aria-labelledby={labelId}
          className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-md border border-slate-300 bg-white py-1 shadow-lg"
        >
          {options.length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate-500" role="presentation">
              {isFetching ? "Searching…" : "No matching doctor"}
            </li>
          ) : (
            options.map((doctor, index) => (
              <li
                key={doctor.id}
                id={`${listboxId}-opt-${index}`}
                role="option"
                aria-selected={doctor.id === value}
                onMouseEnter={() => setActiveIndex(index)}
                // mousedown, not click: the input's blur would otherwise close
                // the list before the click landed.
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(doctor);
                }}
                className={cn(
                  "cursor-pointer px-3 py-2 text-sm",
                  index === activeIndex ? "bg-blue-50" : "bg-white",
                )}
              >
                <span className="font-medium text-slate-900">{doctor.full_name}</span>
                <span className="ml-2 text-xs text-slate-500">
                  {doctor.employee_code}
                </span>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}
