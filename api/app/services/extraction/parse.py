"""Reading the values off a lab report. Phase 7.3.

Three things a report line contains and this module turns into data:

* a **value** — ``9.2``, ``<5``, ``>1000``, ``Positive``, ``Not Detected``
* a **reference range** — ``10-20``, ``< 5``, ``>= 3.5``, and the sex-split
  form ``Male: 13-17 / Female: 12-15``
* a **status stamp** — ``PRELIMINARY`` / ``FINAL`` / ``AMENDED``

## Why censored values are not numbers

``<5`` is not ``5``, and it is not ``4.9`` either. A troponin reported as
``<0.01`` means *"below what this assay can see"*, which is clinically the
opposite of ``0.01``. Storing it as a plain number loses the only part that
mattered, so the operator is kept alongside the magnitude and
:func:`comparable_value` exists for the rare places a single number is needed —
with the censoring visible in its name.

## Why parsing refuses rather than guesses

Every function here returns ``None`` on anything it does not recognise, and
nothing falls back to a "best effort" number. A misparsed decimal point is a
tenfold error in a clinical value; a ``None`` is a review-queue entry. The
asymmetry is the same one Phase 7.5 is built around.
"""

from __future__ import annotations

import dataclasses
import decimal
import re

# ── values ────────────────────────────────────────────────────────────

#: Text results that are not numbers and must not be coerced into one.
QUALITATIVE = {
    "positive": "positive",
    "negative": "negative",
    "reactive": "reactive",
    "non-reactive": "non_reactive",
    "nonreactive": "non_reactive",
    "detected": "detected",
    "not detected": "not_detected",
    "nil": "nil",
    "absent": "absent",
    "present": "present",
    "trace": "trace",
    "normal": "normal",
    "abnormal": "abnormal",
}

# Both Western and Indian digit grouping. `1,50,000` is how an Indian lab
# prints a platelet count of 150,000 -- lakh grouping puts *two* digits in the
# middle groups, not three. A pattern that only knew `\d{3}` groups silently
# failed to parse it, which for a platelet count means a critical
# thrombocytopenia arriving as "unparseable" instead of as a number.
_NUMBER = r"[-+]?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|[-+]?\d*\.\d+|[-+]?\d+"
_VALUE_RE = re.compile(rf"^\s*(?P<op><=|>=|<|>|≤|≥)?\s*(?P<num>{_NUMBER})\s*$")

_OPERATOR_CANON = {"≤": "<=", "≥": ">=", "<": "<", ">": ">", "<=": "<=", ">=": ">="}


@dataclasses.dataclass(frozen=True)
class ParsedValue:
    """A value as the lab reported it, not as we wish it were."""

    raw: str
    #: Set for numeric results. ``None`` for qualitative ones.
    number: decimal.Decimal | None = None
    #: ``<``, ``<=``, ``>``, ``>=`` for censored results; ``None`` when exact.
    operator: str | None = None
    #: Set for qualitative results, normalised to the vocabulary above.
    qualitative: str | None = None

    @property
    def is_censored(self) -> bool:
        return self.operator is not None

    @property
    def is_numeric(self) -> bool:
        return self.number is not None


def parse_value(raw: str | None) -> ParsedValue | None:
    """``"9.2"`` → 9.2 · ``"<0.01"`` → censored 0.01 · ``"Positive"`` → qualitative.

    Returns ``None`` when the text is not recognisable as either, which routes
    the row to a human rather than inventing a number for it.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    lowered = text.casefold()
    if lowered in QUALITATIVE:
        return ParsedValue(raw=text, qualitative=QUALITATIVE[lowered])

    match = _VALUE_RE.match(text)
    if match is None:
        return None

    # Thousands separators are common on cell counts ("1,50,000" in Indian
    # grouping as well as "150,000"). Both strip to the same number.
    number = decimal.Decimal(match.group("num").replace(",", ""))
    operator = match.group("op")
    return ParsedValue(
        raw=text,
        number=number,
        operator=_OPERATOR_CANON[operator] if operator else None,
    )


def comparable_value(parsed: ParsedValue) -> decimal.Decimal | None:
    """The number to compare against a threshold, censoring included.

    Named so the caller cannot use it without noticing. ``<5`` returns 5: for a
    *lower* bound that is conservative (the true value is smaller), and Phase 3's
    rules are written knowing this. It is deliberately **not** a general-purpose
    accessor — ``parsed.number`` is there for anyone who needs the raw magnitude.
    """
    return parsed.number


# ── reference ranges ──────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ReferenceRange:
    low: decimal.Decimal | None = None
    high: decimal.Decimal | None = None
    #: Kept whenever the range is not a plain numeric interval, so nothing that
    #: could not be parsed is silently discarded.
    text: str | None = None
    #: ``male`` / ``female`` when the range was sex-specific.
    sex: str | None = None


_RANGE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(rf"^\s*(?P<low>{_NUMBER})\s*[-–—]\s*(?P<high>{_NUMBER})\s*$"),
        "interval",
    ),
    (re.compile(rf"^\s*(?:<|≤|<=)\s*(?P<high>{_NUMBER})\s*$"), "upper"),
    (re.compile(rf"^\s*(?:>|≥|>=)\s*(?P<low>{_NUMBER})\s*$"), "lower"),
    (re.compile(rf"^\s*up\s+to\s+(?P<high>{_NUMBER})\s*$", re.I), "upper"),
)

_SEX_SPLIT = re.compile(r"(male|female)\s*[:\-]\s*([^/|,;]+)", re.I)


def parse_reference_range(
    raw: str | None, *, sex: str | None = None
) -> ReferenceRange | None:
    """``"10-20"`` · ``"< 5"`` · ``">= 3.5"`` · ``"Male: 13-17 / Female: 12-15"``.

    ``sex`` selects from a sex-specific range. **Without it, a sex-specific range
    is returned as text rather than as one of the two intervals** — picking the
    male range by default would silently flag healthy women as anaemic.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    # Non-numeric ranges ("Negative", "Not Detected") are legitimate and kept
    # whole. There is nothing to compare numerically and pretending otherwise
    # would be worse than saying so.
    if text.casefold() in QUALITATIVE:
        return ReferenceRange(text=text)

    pairs = _SEX_SPLIT.findall(text)
    if pairs:
        if sex is None:
            return ReferenceRange(text=text)
        wanted = sex.strip().casefold()
        for found_sex, portion in pairs:
            if found_sex.casefold() == wanted:
                inner = parse_reference_range(portion.strip())
                if inner is None:
                    return ReferenceRange(text=text, sex=wanted)
                return ReferenceRange(
                    low=inner.low, high=inner.high, text=text, sex=wanted
                )
        return ReferenceRange(text=text)

    for pattern, kind in _RANGE_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        groups = match.groupdict()
        low = groups.get("low")
        high = groups.get("high")
        return ReferenceRange(
            low=decimal.Decimal(low.replace(",", "")) if low else None,
            high=decimal.Decimal(high.replace(",", "")) if high else None,
            text=text if kind not in {"interval", "upper", "lower"} else None,
        )

    # Unrecognised. Kept verbatim rather than dropped -- an admin reviewing the
    # queue needs to see what the lab actually printed.
    return ReferenceRange(text=text)


# ── report status ─────────────────────────────────────────────────────

STATUS_PRELIMINARY = "preliminary"
STATUS_FINAL = "final"
STATUS_AMENDED = "amended"

#: Ordered most-specific first. `AMENDED` must be tested before `FINAL`,
#: because an amended report often says "FINAL (AMENDED)" and matching `FINAL`
#: first would lose the amendment -- which is exactly the case 7.6 says must
#: re-open a closed case.
_STATUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(amended|corrected|revised|addendum)\b", re.I), STATUS_AMENDED),
    (
        re.compile(r"\b(preliminary|provisional|interim|partial)\b", re.I),
        STATUS_PRELIMINARY,
    ),
    (
        re.compile(r"\b(final|complete[d]?|authorised|authorized|verified)\b", re.I),
        STATUS_FINAL,
    ),
)


def parse_report_status(text: str | None) -> str | None:
    """Find the status stamp in a page of report text.

    Returns ``None`` when no stamp is present. **That is not the same as
    ``final``**, and 7.6 relies on the difference: an unstamped report has not
    told us it is complete, so it must not supersede anything.
    """
    if not text:
        return None
    for pattern, status in _STATUS_PATTERNS:
        if pattern.search(text):
            return status
    return None


# ── narrative sections ────────────────────────────────────────────────

#: The headings radiology and histopathology reports actually use.
_SECTION_HEADINGS = (
    "clinical history",
    "clinical details",
    "technique",
    "comparison",
    "findings",
    "impression",
    "conclusion",
    "comment",
    "recommendation",
    "microscopy",
    "macroscopy",
    "gross",
    "diagnosis",
)

_HEADING_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in _SECTION_HEADINGS) + r")\s*[:\-–]?\s*$",
    re.I | re.M,
)


def split_sections(text: str) -> dict[str, str]:
    """Segment a narrative report into its named sections.

    Everything before the first recognised heading is returned under
    ``preamble`` rather than discarded — a report whose headings we do not know
    must still reach Phase 3's keyword scan in full, or a finding in an
    unfamiliar section would simply vanish.
    """
    if not text or not text.strip():
        return {}

    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return {"preamble": text.strip()}

    sections: dict[str, str] = {}
    first = matches[0]
    preamble = text[: first.start()].strip()
    if preamble:
        sections["preamble"] = preamble

    for index, match in enumerate(matches):
        name = match.group(1).strip().casefold().replace(" ", "_")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        # A heading repeated later in the report appends rather than replaces,
        # so a second "Impression" block cannot silently delete the first.
        sections[name] = f"{sections[name]}\n\n{body}" if name in sections else body

    return sections
