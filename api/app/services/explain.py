"""Rule output → plain language. Phase 5.2.

    rule engine output **in plain language**: *"Amoxicillin-clavulanate
    (discharge medication) is Resistant for E. coli"*

That example is the specification. The doctor opening a case at 2am must not
have to know that ``CULT_RESISTANT_TO_DISCHARGE_DRUG`` means their patient
went home on the wrong antibiotic.

**This is plain code, not AI, and it must stay that way.** RULE 1: the safety
property never depends on AI. A sentence rendered from a reason code and the
inputs the rule actually used is reproducible, testable, and identical six
months later; a generated one is none of those.

Two rules it follows:

* **Never invent a fact.** Every sentence is built only from
  ``reason_code``, ``inputs_used`` and ``detail`` — the values the rule
  genuinely saw. An unknown reason code falls back to showing the code itself
  rather than guessing, because a wrong explanation is worse than a terse one.
* **Never soften a CRITICAL.** The severity sets the tone, and the phrasing
  for a critical finding says what is wrong without hedging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SEVERITY_CRITICAL = "critical"
SEVERITY_FOLLOW_UP = "follow_up"
SEVERITY_NORMAL = "normal"


@dataclass(frozen=True)
class Explanation:
    """One rule's finding, rendered for a human."""

    severity: str
    rule_id: str
    reason_code: str
    headline: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "rule_id": self.rule_id,
            "reason_code": self.reason_code,
            "headline": self.headline,
            "detail": self.detail,
        }


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _value_with_unit(inputs: dict[str, Any]) -> str:
    raw = inputs.get("value_raw") or inputs.get("value_numeric")
    unit = inputs.get("unit_normalized") or inputs.get("unit_raw")
    if raw is None:
        return "the value"
    return f"{raw} {unit}".strip() if unit else str(raw)


def _range_phrase(inputs: dict[str, Any]) -> str:
    low, high = inputs.get("ref_low"), inputs.get("ref_high")
    if low is not None and high is not None:
        return f"reference range {low}–{high}"
    if high is not None:
        return f"reference upper limit {high}"
    if low is not None:
        return f"reference lower limit {low}"
    return "no reference range"


def _test_name(inputs: dict[str, Any]) -> str:
    return _s(inputs.get("test_name_raw") or inputs.get("test_code") or "This test")


def _numeric(
    reason: str, inputs: dict[str, Any], detail: dict[str, Any]
) -> tuple[str, str | None]:
    test = _test_name(inputs)
    value = _value_with_unit(inputs)
    rng = _range_phrase(inputs)

    if reason == "NUM_ABOVE_CRITICAL_HIGH":
        bound = detail.get("critical_high")
        return (
            f"{test} is {value} — above the critical threshold of {bound}.",
            "A panic-range value. The lab's own critical limit has been crossed.",
        )
    if reason == "NUM_BELOW_CRITICAL_LOW":
        bound = detail.get("critical_low")
        return (
            f"{test} is {value} — below the critical threshold of {bound}.",
            "A panic-range value. The lab's own critical limit has been crossed.",
        )
    if reason == "NUM_FAR_ABOVE_REFERENCE_RANGE":
        factor = detail.get("multiplier")
        return (
            f"{test} is {value}, far above the {rng}.",
            f"More than {factor}× the upper limit." if factor else None,
        )
    if reason == "NUM_FAR_BELOW_REFERENCE_RANGE":
        factor = detail.get("multiplier")
        return (
            f"{test} is {value}, far below the {rng}.",
            f"Less than one {factor}th of the lower limit." if factor else None,
        )
    if reason == "NUM_ABOVE_REFERENCE_RANGE":
        return (f"{test} is {value}, above the {rng}.", None)
    if reason == "NUM_BELOW_REFERENCE_RANGE":
        return (f"{test} is {value}, below the {rng}.", None)
    if reason == "NUM_WITHIN_REFERENCE_RANGE":
        return (f"{test} is {value}, within the {rng}.", None)
    if reason == "NUM_NO_REFERENCE_RANGE":
        return (
            f"{test} is {value}, but the report carried no reference range.",
            "Flagged for review because the value cannot be judged automatically.",
        )
    if reason == "NUM_NO_NUMERIC_VALUE":
        return (
            f"{test} has no numeric value that could be compared.",
            "Raised for a human to read rather than assumed to be normal.",
        )
    if reason == "NUM_CENSORED_NOT_COMPARABLE":
        return (
            f"{test} is reported as {_s(inputs.get('value_raw'))}, which cannot be "
            f"compared against the {rng}.",
            "A censored value ('<' or '>') that does not settle the question.",
        )
    return ("", None)


def _culture(
    reason: str, inputs: dict[str, Any], detail: dict[str, Any]
) -> tuple[str, str | None]:
    organism = _s(inputs.get("organism") or "The organism")
    drug = detail.get("offending_drug")
    discharge = inputs.get("discharge_antibiotics") or []

    if reason == "CULT_RESISTANT_TO_DISCHARGE_DRUG":
        # The plan's own worked example.
        return (
            f"{drug} (discharge medication) is Resistant for {organism}.",
            "The patient went home on an antibiotic this organism is resistant "
            "to. The prescription needs review."
            + (
                ""
                if detail.get("alternatives_available")
                else " No sensitive alternative appears on the panel."
            ),
        )
    if reason == "CULT_INTERMEDIATE_TO_DISCHARGE_DRUG":
        return (
            f"{drug} (discharge medication) is Intermediate for {organism}.",
            "Intermediate is not sensitive. The dose or the drug may need to "
            "change.",
        )
    if reason == "CULT_MULTI_DRUG_RESISTANT_ORGANISM":
        return (
            f"{organism} meets a multi-drug-resistant pattern.",
            _s(detail.get("mdro_rule_name") or detail.get("mdro_code")) or None,
        )
    if reason == "CULT_NO_DISCHARGE_ANTIBIOTIC":
        return (
            f"{organism} was grown, and no antibiotic was prescribed on discharge.",
            "There is nothing to check the sensitivities against, so this needs "
            "a clinician's eye.",
        )
    if reason == "CULT_DISCHARGE_DRUG_NOT_ON_PANEL":
        return (
            f"The discharge antibiotic was not tested against {organism}.",
            (
                f"Prescribed: {', '.join(str(d) for d in discharge)}. The lab's "
                "panel did not include it, so cover is unknown."
                if discharge
                else None
            ),
        )
    if reason == "CULT_NO_SENSITIVITY_DATA":
        return (
            f"{organism} was grown, but the report carried no sensitivities.",
            "Cover cannot be confirmed without a sensitivity panel.",
        )
    if reason == "CULT_COVERED_BY_DISCHARGE_DRUGS":
        return (
            f"{organism} is sensitive to the discharge antibiotic.",
            (
                f"Prescribed: {', '.join(str(d) for d in discharge)}."
                if discharge
                else None
            ),
        )
    if reason == "CULT_LIKELY_CONTAMINANT":
        specimen = _s(inputs.get("specimen_type"))
        return (
            f"{organism} at {_s(inputs.get('colony_count')) or 'a low count'} "
            "looks like a contaminant.",
            f"Specimen: {specimen}." if specimen else None,
        )
    if reason == "CULT_NO_GROWTH":
        return ("No growth.", None)
    return ("", None)


def _narrative(
    reason: str, inputs: dict[str, Any], detail: dict[str, Any]
) -> tuple[str, str | None]:
    terms = detail.get("matched_terms") or []
    sentences = detail.get("matched_sentences") or []
    section = _s(inputs.get("section") or "the report")
    term_list = ", ".join(f"'{t}'" for t in terms)

    if reason == "NARR_KEYWORD_MATCH":
        quoted = sentences[0] if sentences else None
        return (
            (
                f"The {section} mentions {term_list}."
                if terms
                else f"The {section} contains a flagged finding."
            ),
            f"“{quoted}”" if quoted else None,
        )
    if reason == "NARR_HEDGED_FINDING":
        return (
            f"The {section} mentions {term_list}, but hedged.",
            "Words like 'possible' or 'cannot exclude' mean this is not a "
            "confirmed finding — and not something to dismiss either.",
        )
    if reason == "NARR_ALL_HITS_NEGATED":
        negated = detail.get("negated_terms") or []
        return (
            (
                f"The {section} explicitly rules out "
                f"{', '.join(str(t) for t in negated)}."
                if negated
                else f"Every flagged term in the {section} was negated."
            ),
            None,
        )
    if reason == "NARR_NO_KEYWORD_MATCH":
        return (f"No flagged findings in the {section}.", None)
    if reason == "NARR_NO_TEXT":
        return ("The report carried no readable narrative text.", None)
    return ("", None)


def _orchestrator(
    reason: str, inputs: dict[str, Any], detail: dict[str, Any]
) -> tuple[str, str | None]:
    if reason == "ORCH_RULE_FAILED":
        return (
            "A rule could not be evaluated, so this case was raised for review.",
            "The system degraded to FOLLOW_UP rather than going silent. "
            f"Error type: {_s(detail.get('error_type') or inputs.get('error_type'))}.",
        )
    if reason == "ORCH_NO_CLASSIFIABLE_CONTENT":
        return (
            "The report had nothing the rules could classify.",
            "Raised for a human to read rather than closed as normal.",
        )
    return ("", None)


_RENDERERS = (
    ("NUM_", _numeric),
    ("CULT_", _culture),
    ("NARR_", _narrative),
    ("ORCH_", _orchestrator),
)


def explain_output(output: dict[str, Any]) -> Explanation:
    """Render one ``rule_outputs`` entry."""
    reason = _s(output.get("reason_code"))
    inputs = output.get("inputs_used") or {}
    detail = output.get("detail") or {}
    if not isinstance(inputs, dict):
        inputs = {}
    if not isinstance(detail, dict):
        detail = {}

    headline, extra = "", None
    for prefix, renderer in _RENDERERS:
        if reason.startswith(prefix):
            headline, extra = renderer(reason, inputs, detail)
            break

    if not headline:
        # Unknown code. Show it rather than inventing a sentence -- a new rule
        # shipped without a phrasing must degrade to "terse", never to "wrong".
        headline = f"Rule {_s(output.get('rule_id'))} returned {reason}."

    return Explanation(
        severity=_s(output.get("severity")),
        rule_id=_s(output.get("rule_id")),
        reason_code=reason,
        headline=headline,
        detail=extra,
    )


# Ordered worst-first so the reason the case is open leads the list.
_SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_FOLLOW_UP: 1, SEVERITY_NORMAL: 2}


def explain_classification(rule_outputs: Any) -> list[Explanation]:
    """Render a whole ``classifications.rule_outputs`` array, worst first."""
    if not isinstance(rule_outputs, list):
        return []
    explained = [
        explain_output(item) for item in rule_outputs if isinstance(item, dict)
    ]
    return sorted(explained, key=lambda e: _SEVERITY_ORDER.get(e.severity, 3))


def summarise(severity: str | None, explanations: list[Explanation]) -> str:
    """One line for the worklist row and the notification body."""
    if not explanations:
        return "Awaiting classification."
    driving = explanations[0]
    if severity == SEVERITY_CRITICAL:
        return f"CRITICAL — {driving.headline}"
    if severity == SEVERITY_FOLLOW_UP:
        return f"Follow-up — {driving.headline}"
    return driving.headline
