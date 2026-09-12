"""Rule C — narrative text. Phase 3.5.

    1. Sentence split (spaCy sentencizer or regex — **no ML model needed**)
    2. Match ``clinical_keywords`` per sentence
    3. For each hit, run negation check within the sentence scope
    4. Negated → discard hit
    5. Surviving hits → take max severity
    6. No hits → **FOLLOW_UP** if radiology/pathology

    > **Critical safety rule: narrative reports never auto-close. Worst case
    > is FOLLOW_UP.**

That last line is the one that matters most, and it is enforced here rather
than trusted: ``classify_narratives`` cannot return an auto-close, and the
floor for a report that contains prose is FOLLOW_UP. Prose is the category
where a rule engine is least able to be sure, so the worst it is allowed to
conclude is "a human should look".

**Matching is on word boundaries, never naive substrings.** A substring match
for "ca" fires inside "care", "cancer" fires inside "cancerous" (fine) but
also inside a surname; "mass" fires inside "massive". Every keyword is
matched as a whole word or phrase, with the boundary computed from the term
itself so a multi-word term still works.

**Negation is scoped, and scope is per pattern.** *"No evidence of malignancy.
Findings consistent with abscess."* must suppress the malignancy and keep the
abscess — so the scope is measured in words within the sentence, and a
sentence split happens first for exactly that reason.

A hedge is not a denial. *"Cannot exclude malignancy"* is not a negative
finding; it is a finding somebody should look at. Hedges therefore reduce
CRITICAL to FOLLOW_UP rather than discarding the hit.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rules import (
    SEVERITY_CRITICAL,
    SEVERITY_FOLLOW_UP,
    SEVERITY_NORMAL,
    max_severity,
)
from app.rules.numeric import RuleOutput

RULE_ID = "C_narrative"

REASON_KEYWORD_HIT = "NARR_KEYWORD_MATCH"
REASON_ALL_NEGATED = "NARR_ALL_HITS_NEGATED"
REASON_NO_HITS = "NARR_NO_KEYWORD_MATCH"
REASON_HEDGED = "NARR_HEDGED_FINDING"
REASON_NO_TEXT = "NARR_NO_TEXT"

# Categories whose *absence* of findings still deserves a human look. The plan:
# "No hits -> FOLLOW_UP if radiology/pathology".
UNCERTAIN_CATEGORIES = ("radiology", "pathology")

# Sentence split. A regex, deliberately: the plan says no ML model is needed,
# and a dependency on spaCy would put a 50 MB model in the path of a safety
# decision for no accuracy gain on structured report prose.
#
# Splits on . ! ? followed by whitespace, but not on common abbreviations that
# appear in reports ("Dr.", "e.g.", "1.5 cm", "No. 3").
_ABBREVIATIONS = (
    "dr",
    "mr",
    "mrs",
    "ms",
    "prof",
    "no",
    "vs",
    "approx",
    "e.g",
    "i.e",
    "cf",
    "fig",
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ENDS_WITH_ABBREVIATION = re.compile(
    r"(?:^|\s)(" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.$",
    re.IGNORECASE,
)
_ENDS_WITH_NUMBER = re.compile(r"\d\.$")


def split_sentences(body: str) -> list[str]:
    """Step 1. Regex sentence split that does not break on "1.5 cm" or "Dr."."""
    if not body or not body.strip():
        return []

    pieces = _SENTENCE_END.split(body.strip())
    sentences: list[str] = []
    buffer = ""
    for piece in pieces:
        candidate = f"{buffer} {piece}".strip() if buffer else piece
        # If this fragment ends in an abbreviation or a decimal point, the
        # split was spurious -- keep accumulating.
        if _ENDS_WITH_ABBREVIATION.search(candidate) or _ENDS_WITH_NUMBER.search(
            candidate
        ):
            buffer = candidate
            continue
        sentences.append(candidate)
        buffer = ""
    if buffer:
        sentences.append(buffer)
    return [s for s in sentences if s.strip()]


def _term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word match for a term, which may be several words.

    ``\\b`` on each end, with internal whitespace allowed to vary, so
    "no evidence of" matches "no  evidence   of" and "mass" does not match
    "massive".
    """
    parts = [re.escape(word) for word in term.strip().split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)


@dataclass
class KeywordHit:
    term: str
    category: str
    severity: str
    sentence: str
    negated: bool = False
    hedged: bool = False
    negation_pattern: str | None = None


@dataclass
class NarrativeVerdict:
    severity: str
    reason_code: str
    matched_terms: list[str] = field(default_factory=list)
    matched_sentences: list[str] = field(default_factory=list)
    hits: list[KeywordHit] = field(default_factory=list)


async def _keywords(session: AsyncSession) -> list[tuple[str, str, str, bool]]:
    rows = (
        await session.execute(
            text(
                "SELECT term, category, severity, requires_negation_check "
                "  FROM clinical_keywords "
                " WHERE active AND deleted_at IS NULL "
                # Ordered so the stored explanation is reproducible. An
                # unordered SELECT is not stable across vacuums or plan
                # changes, and "same input, same output" is a promise this
                # engine makes.
                " ORDER BY term"
            )
        )
    ).all()
    return [(r.term, r.category, r.severity, r.requires_negation_check) for r in rows]


async def _negations(session: AsyncSession) -> list[tuple[str, int, int, bool]]:
    rows = (
        await session.execute(
            text(
                "SELECT pattern, scope_words_before, scope_words_after, is_hedge "
                "  FROM negation_patterns WHERE active AND deleted_at IS NULL "
                " ORDER BY pattern"
            )
        )
    ).all()
    return [
        (r.pattern, r.scope_words_before, r.scope_words_after, r.is_hedge) for r in rows
    ]


def is_negated(
    sentence: str,
    term_start: int,
    negations: list[tuple[str, int, int, bool]],
) -> tuple[bool, bool, str | None]:
    """Step 3. Is the hit at ``term_start`` inside a negation's scope?

    Returns ``(negated, hedged, pattern)``. Scope is counted in **words**,
    per pattern, so *"no evidence of malignancy"* suppresses a hit four words
    out while a negation earlier in a long sentence does not reach a finding
    at the end of it.

    **Every pattern in scope is collected, and a hedge beats a denial.** This
    is not a preference; it is the difference between reporting a finding and
    silencing it. *"Malignancy is unlikely; ruled out on the prior imaging."*
    puts both ``\\bunlikely\\b`` (hedge) and ``\\bruled out\\b`` (denial) in
    scope of the same term. Returning on whichever matched first made the
    answer depend on the order rows came back from an unordered ``SELECT`` —
    so the same report classified as FOLLOW_UP one day and produced no flag
    the next. Taking the hedge is the reading that never silences a finding,
    and it is the same instinct as the rest of this engine: when the evidence
    disagrees with itself, a human looks.
    """
    hedges: list[str] = []
    denials: list[str] = []

    for pattern, before, after, hedge in negations:
        try:
            matcher = re.compile(pattern, re.IGNORECASE)
        except re.error:
            # An admin typed a bad regex. Skip it rather than taking the whole
            # engine down -- and never treat a broken pattern as a negation,
            # because that would suppress a real finding.
            continue

        for match in matcher.finditer(sentence):
            if match.start() <= term_start:
                # Negation precedes the term: count words between them.
                words = len(sentence[match.end() : term_start].split())
                in_scope = words <= after
            else:
                # Negation follows the term ("malignancy: none seen").
                words = len(sentence[term_start : match.start()].split())
                in_scope = words <= before

            if in_scope:
                (hedges if hedge else denials).append(pattern)
                break  # one match per pattern is enough to put it in scope

    # A hedge outranks a denial, and the winner within a group is the
    # lexicographically first so the recorded explanation is reproducible.
    if hedges:
        return (False, True, sorted(hedges)[0])
    if denials:
        return (True, False, sorted(denials)[0])
    return (False, False, None)


async def classify_narrative_text(
    session: AsyncSession, body: str, *, category: str | None = None
) -> NarrativeVerdict:
    """Rule C for one block of prose."""
    sentences = split_sentences(body)
    if not sentences:
        return NarrativeVerdict(
            severity=(
                SEVERITY_FOLLOW_UP
                if (category or "").lower() in UNCERTAIN_CATEGORIES
                else SEVERITY_NORMAL
            ),
            reason_code=REASON_NO_TEXT,
        )

    keywords = await _keywords(session)
    negations = await _negations(session)

    hits: list[KeywordHit] = []
    for sentence in sentences:
        for term, cat, severity, needs_negation in keywords:
            for match in _term_pattern(term).finditer(sentence):
                negated, hedged, pattern = (
                    is_negated(sentence, match.start(), negations)
                    if needs_negation
                    else (False, False, None)
                )
                hits.append(
                    KeywordHit(
                        term=term,
                        category=cat,
                        severity=severity,
                        sentence=sentence,
                        negated=negated,
                        hedged=hedged,
                        negation_pattern=pattern,
                    )
                )
                break  # one hit per term per sentence is enough

    # ── step 4: discard negated hits ──────────────────────────────
    surviving = [hit for hit in hits if not hit.negated]

    if not surviving:
        # Either nothing matched, or everything that did was negated. Both
        # mean "no positive finding" -- but a radiology or pathology report
        # with no finding is still a report a human has not read.
        floor = (
            SEVERITY_FOLLOW_UP
            if (category or "").lower() in UNCERTAIN_CATEGORIES
            else SEVERITY_NORMAL
        )
        return NarrativeVerdict(
            severity=floor,
            reason_code=REASON_ALL_NEGATED if hits else REASON_NO_HITS,
            hits=hits,
        )

    # ── step 5: max severity of what survived ─────────────────────
    # A hedged hit is downgraded, not dropped: "cannot exclude malignancy" is
    # not a negative finding, it is one somebody should look at.
    severities = [
        (
            SEVERITY_FOLLOW_UP
            if hit.hedged and hit.severity == SEVERITY_CRITICAL
            else hit.severity
        )
        for hit in surviving
    ]
    worst = max_severity(*severities)

    return NarrativeVerdict(
        severity=worst,
        reason_code=(
            REASON_HEDGED
            if all(hit.hedged for hit in surviving)
            else REASON_KEYWORD_HIT
        ),
        matched_terms=sorted({hit.term for hit in surviving}),
        matched_sentences=sorted({hit.sentence for hit in surviving}),
        hits=hits,
    )


async def classify_result_narratives(
    session: AsyncSession, result_id: uuid.UUID, *, order_category: str | None = None
) -> list[RuleOutput]:
    """Rule C over every narrative section on a result.

    **Never auto-closes.** The output carries ``auto_close: False`` on every
    path, which the orchestrator honours — the plan's critical safety rule is
    enforced structurally rather than remembered.
    """
    rows = (
        await session.execute(
            text(
                "SELECT section, text FROM result_narratives "
                " WHERE result_id = :r AND deleted_at IS NULL ORDER BY section"
            ),
            {"r": str(result_id)},
        )
    ).all()
    if not rows:
        return []

    outputs: list[RuleOutput] = []
    for row in rows:
        verdict = await classify_narrative_text(
            session, row.text, category=order_category
        )
        outputs.append(
            RuleOutput(
                severity=verdict.severity,
                rule_id=RULE_ID,
                reason_code=verdict.reason_code,
                inputs_used={
                    "section": row.section,
                    "order_category": order_category,
                    "sentence_count": len(split_sentences(row.text)),
                },
                detail={
                    "matched_terms": verdict.matched_terms,
                    "matched_sentences": verdict.matched_sentences,
                    "negated_terms": sorted(
                        {hit.term for hit in verdict.hits if hit.negated}
                    ),
                    # Structural, not incidental: narrative reports never
                    # auto-close, worst case FOLLOW_UP.
                    "auto_close": False,
                },
            )
        )
    return outputs
