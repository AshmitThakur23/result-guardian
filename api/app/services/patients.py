"""Patient lookup. Phase 1.5. Read-only.

One query serves MRN, name and phone. A ward clerk types what they have --
"MRN-4410", "sunita", "9876543210" -- and should not have to tell the system
which of the three it was.

Name matching uses the Phase 1.2 trigram index
(``ix_patients_name_trgm``, GIN ``gin_trgm_ops``), which serves both the
similarity operator and the substring ILIKE below. Nothing speculative is
added here: no tsvector column, no search table, no ranking service.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import Integer, case, cast, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.encounters import Encounter
from app.db.models.patients import Patient
from app.schemas.patients import (
    PatientEncounterRow,
    PatientSearchRow,
    PatientWithEncounters,
)

MAX_SEARCH_RESULTS = 50

# Below this, trigram similarity is noise -- "ab" is similar to half the ward.
# Short queries fall through to the substring match instead.
MIN_TRIGRAM_QUERY_CHARS = 4

# pg_trgm's default is 0.3, which misses "sunta" for "Sunita". Set per-query
# rather than globally so nothing else in the system inherits it.
TRIGRAM_THRESHOLD = 0.25

_NON_DIGITS = re.compile(r"\D+")


def _escape_like(value: str) -> str:
    """Neutralise LIKE wildcards in user input.

    Without this, a query of ``%`` matches every patient in the hospital --
    a one-character way to dump the patient index. The backslash is escaped
    first, or it would escape the escapes added after it.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _digits(value: str) -> str:
    return _NON_DIGITS.sub("", value)


async def search_patients(
    session: AsyncSession,
    query: str,
    limit: int = 20,
) -> list[PatientSearchRow]:
    """Find patients by MRN, name or phone.

    Results are ranked so the unambiguous match wins: an exact MRN first, then
    an MRN prefix, then phone, then name. A clerk who typed a full MRN should
    never have to scroll past a fuzzy name hit to find it.
    """
    text_query = query.strip()
    if not text_query:
        # The router requires a non-empty q, so this is only reachable from a
        # string of pure whitespace. Returning nothing beats returning the
        # entire patient index.
        return []

    pattern = f"%{_escape_like(text_query)}%"
    phone_digits = _digits(text_query)
    # Seven digits is the shortest thing that identifies a person by phone;
    # below that this would match on an area code.
    phone_usable = len(phone_digits) >= 7
    phone_pattern = f"%{phone_digits}" if phone_usable else None

    name_match: ColumnElement[bool]
    name_rank: ColumnElement[Any]
    if len(text_query) >= MIN_TRIGRAM_QUERY_CHARS:
        # pg_trgm's `%` operator reads this threshold. Set per call, so no
        # other query in the system inherits a looser definition of "similar".
        #
        # CAST(... AS real), never `::real`: set_limit is declared to take
        # float4 and asyncpg sends float8, so the bare bind param fails with
        # "function set_limit(double precision) does not exist". And a `::`
        # inside a text() would be read as a *second* bind parameter -- a trap
        # this project has already been caught by twice.
        await session.execute(
            text("SELECT set_limit(CAST(:threshold AS real))"),
            {"threshold": TRIGRAM_THRESHOLD},
        )
        name_match = or_(
            Patient.name.op("%")(text_query),
            Patient.name.ilike(pattern, escape="\\"),
        )
        name_rank = func.similarity(Patient.name, text_query)
    else:
        name_match = Patient.name.ilike(pattern, escape="\\")
        name_rank = literal(0.0)

    mrn_exact = func.lower(Patient.mrn) == text_query.lower()
    mrn_partial = Patient.mrn.ilike(pattern, escape="\\")

    # Compare digits to digits: the column is E.164 ("+919876543210") but a
    # clerk types what is written on the file ("98765 43210").
    phone_match = (
        or_(
            func.regexp_replace(Patient.phone_primary_e164, r"\D", "", "g").like(
                phone_pattern
            ),
            func.regexp_replace(Patient.phone_alt_e164, r"\D", "", "g").like(
                phone_pattern
            ),
        )
        if phone_pattern is not None
        else None
    )

    conditions = [mrn_exact, mrn_partial, name_match]
    if phone_match is not None:
        conditions.append(phone_match)

    # Lower sorts first: an unambiguous identifier beats a fuzzy name every
    # time. A clerk who typed a full MRN must never have to scroll past a
    # similar-sounding name to find it.
    rank = case(
        (mrn_exact, 0),
        (mrn_partial, 1),
        *([(phone_match, 2)] if phone_match is not None else []),
        else_=3,
    )

    # How many of this patient's encounters are still open -- the clerk's
    # actual question is usually "which admission do I need?".
    active_encounters = (
        select(func.count())
        .select_from(Encounter)
        .where(
            Encounter.patient_id == Patient.id,
            Encounter.status == "active",
            Encounter.deleted_at.is_(None),
        )
        .correlate(Patient)
        .scalar_subquery()
    )

    stmt = (
        select(
            Patient.id,
            Patient.mrn,
            Patient.name,
            Patient.dob,
            Patient.sex,
            Patient.phone_primary_e164,
            cast(active_encounters, Integer).label("active_encounter_count"),
        )
        .where(Patient.deleted_at.is_(None), or_(*conditions))
        .order_by(rank, name_rank.desc(), Patient.name)
        .limit(min(limit, MAX_SEARCH_RESULTS))
    )

    rows = (await session.execute(stmt)).mappings().all()
    return [PatientSearchRow.model_validate(dict(row)) for row in rows]


class PatientNotFoundError(LookupError):
    """No such patient. The router turns this into a 404."""


async def get_patient_with_encounters(
    session: AsyncSession, patient_id: uuid.UUID
) -> PatientWithEncounters:
    """One patient and their encounters, newest admission first."""
    patient = (
        (
            await session.execute(
                select(
                    Patient.id,
                    Patient.mrn,
                    Patient.name,
                    Patient.dob,
                    Patient.sex,
                    Patient.phone_primary_e164,
                ).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
            )
        )
        .mappings()
        .first()
    )

    if patient is None:
        raise PatientNotFoundError(str(patient_id))

    encounters = (
        (
            await session.execute(
                select(
                    Encounter.id,
                    Encounter.encounter_no,
                    Encounter.type,
                    Encounter.status,
                    Encounter.admitted_at,
                    Encounter.discharged_at,
                    Encounter.ward,
                    Encounter.bed,
                )
                .where(
                    Encounter.patient_id == patient_id,
                    Encounter.deleted_at.is_(None),
                )
                .order_by(Encounter.admitted_at.desc())
            )
        )
        .mappings()
        .all()
    )

    rows = [PatientEncounterRow.model_validate(dict(row)) for row in encounters]
    return PatientWithEncounters(
        patient=PatientSearchRow.model_validate(
            dict(patient)
            | {"active_encounter_count": sum(1 for e in rows if e.status == "active")}
        ),
        encounters=rows,
    )
