"""Development seed data. Phase 1.5.

    "Seed script: 20 patients, 60 orders in varied states, 10 doctors"

⚠️  **DEVELOPMENT AND TEST ONLY.** This writes clearly fake people into the
database. It refuses to run when ``RG_ENV`` is ``prod``, and every row it
writes is branded so it can never be mistaken for a real record:

* names are drawn from a fixed list and suffixed ``(SEED)``
* MRNs are ``SEED-####``, encounter numbers ``SEED-ENC-####``
* phone numbers are in the **555** range reserved for fiction, formatted E.164
  with the ``+91`` country code the schema expects
* employee codes are ``SEED-D##``

**Deterministic.** Every id is derived from a fixed namespace UUID and a
stable key, so running this twice produces the *same* rows rather than a
second set of people. That is what makes it safe to re-run: it upserts by
primary key, so a developer who runs it after adding an order keeps their
work. Use ``--reset`` to soft-delete a previous seed first.

Nothing here touches a patient's consent columns, and nothing here is a
fixture for the automated suite -- the tests build their own rows. This exists
so a human can open the app and have something to click.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import uuid
import zlib
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models.discharge import DischargeMedication
from app.db.models.encounters import Encounter
from app.db.models.orders import Order
from app.db.models.organisation import Department, User
from app.db.models.patients import Patient

# Every seeded row's id is derived from this namespace plus a stable key, so
# the script is idempotent without needing to read anything back first.
SEED_NAMESPACE = uuid.UUID("5eed0000-0000-4000-8000-00000000d0c5")

# A fixed instant the seeded ids are timestamped from: 2020-01-01T00:00:00Z in
# milliseconds. Real rows use uuid7() off the wall clock; seeded rows need the
# same *shape* (UUIDv7, time-sortable, per the project's PK convention) while
# staying byte-identical across runs, so the timestamp is pinned instead.
SEED_EPOCH_MS = 1577836800000

SEED_TAG = "(SEED)"
MRN_PREFIX = "SEED-"
ENCOUNTER_PREFIX = "SEED-ENC-"
EMPLOYEE_PREFIX = "SEED-D"
DEPARTMENT_CODE = "SEED-MED"

PATIENT_COUNT = 20
DOCTOR_COUNT = 10
ORDER_COUNT = 60

HOUR = dt.timedelta(hours=1)


def seed_id(kind: str, index: int) -> uuid.UUID:
    """Stable **UUIDv7** for a seeded row. Same input, same uuid, every run.

    Deterministic and still version 7: the 48-bit timestamp is derived from a
    pinned epoch rather than the clock, and the remaining bits come from a
    uuid5 digest of the key. Honours the project's time-sortable PK convention
    without making the seed non-reproducible.
    """
    digest = uuid.uuid5(SEED_NAMESPACE, f"{kind}:{index}").bytes
    # One millisecond per (kind, index) pair keeps seeded rows sortable in the
    # order they are written, like real uuid7 values would be.
    stamp = SEED_EPOCH_MS + (zlib.crc32(kind.encode()) % 100000) * 1000 + index
    raw = bytearray(digest)
    raw[0:6] = stamp.to_bytes(6, "big")
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(raw))


# Obviously-synthetic names. Indian-context, because the product is, but drawn
# from a fixed list rather than generated -- a random generator eventually
# produces a real person's name by accident.
_GIVEN = (
    "Aarav",
    "Diya",
    "Kabir",
    "Ananya",
    "Vivaan",
    "Ishita",
    "Reyansh",
    "Myra",
    "Arjun",
    "Saanvi",
    "Advait",
    "Aadhya",
    "Krish",
    "Navya",
    "Dhruv",
    "Kiara",
    "Ayaan",
    "Prisha",
    "Rudra",
    "Anika",
)
_FAMILY = (
    "Sharma",
    "Iyer",
    "Nair",
    "Reddy",
    "Banerjee",
    "Kulkarni",
    "Patel",
    "Menon",
    "Chauhan",
    "Bose",
    "Mehta",
    "Rao",
    "Gupta",
    "Joshi",
    "Desai",
    "Verma",
    "Pillai",
    "Sinha",
    "Kaur",
    "Naidu",
)

_DOCTOR_GIVEN = (
    "Asha",
    "Ravi",
    "Meera",
    "Sanjay",
    "Latha",
    "Imran",
    "Nisha",
    "Vikram",
    "Fatima",
    "Gopal",
)
_DOCTOR_FAMILY = (
    "Menon",
    "Kulkarni",
    "Iyer",
    "Bhatt",
    "Krishnan",
    "Sheikh",
    "Rao",
    "Chandra",
    "Ansari",
    "Varma",
)

# (test_code, test_name, category, expected_tat_hours)
_TESTS = (
    ("CBC", "Complete Blood Count", "lab", Decimal("4")),
    ("CREA", "Serum Creatinine", "lab", Decimal("6")),
    ("HBA1C", "HbA1c", "lab", Decimal("24")),
    ("TSH", "Thyroid Stimulating Hormone", "lab", Decimal("24")),
    ("URC", "Urine Culture", "micro", Decimal("48")),
    ("BLDC", "Blood Culture", "micro", Decimal("72")),
    ("CXR", "Chest X-Ray", "radiology", Decimal("2")),
    ("USG", "Ultrasound Abdomen", "radiology", Decimal("6")),
    ("HPE", "Histopathology", "pathology", Decimal("120")),
    ("LFT", "Liver Function Test", "lab", Decimal("6")),
)

# Deliberately varied, and deliberately weighted towards outstanding: the
# point of the seed is to give the discharge gate something to block on.
_ORDER_STATUSES = (
    "ordered",
    "ordered",
    "collected",
    "in_lab",
    "in_lab",
    "preliminary",
    "final",
    "final",
    "cancelled",
    "rejected",
)

_WARDS = ("Ward 1", "Ward 2", "Ward 3", "ICU", "Day Care")
_ENCOUNTER_TYPES = ("ipd", "ipd", "ipd", "emergency", "daycare")

# ATC codes are real classification codes, not patient data.
_MEDICATIONS = (
    ("Amoxicillin", "J01CA04", "500 mg", "oral", "TDS", Decimal("5"), True),
    ("Azithromycin", "J01FA10", "500 mg", "oral", "OD", Decimal("3"), True),
    ("Metformin", "A10BA02", "500 mg", "oral", "BD", Decimal("30"), False),
    ("Atorvastatin", "C10AA05", "10 mg", "oral", "HS", Decimal("30"), False),
    ("Pantoprazole", "A02BC02", "40 mg", "oral", "OD", Decimal("14"), False),
)


def _phone(index: int) -> str:
    """A 555-range number, which by convention is not a real subscriber."""
    return f"+9155500{index:05d}"


async def _seed(session: AsyncSession, now: dt.datetime) -> dict[str, int]:
    counts: dict[str, int] = {}

    # ── department ────────────────────────────────────────────────
    department_id = seed_id("department", 0)
    unit_head_id = seed_id("doctor", 0)

    # ── doctors ───────────────────────────────────────────────────
    # users.department_id -> departments.id and departments.unit_head_user_id
    # -> users.id, so the two tables reference each other. Write the users
    # without a department first, then the department, then attach them.
    #
    # Index 0 doubles as the department's unit head, so the Phase 1.3 override
    # path has somewhere to flag a case to. Without it, an override on seeded
    # data 409s with "no unit head".
    doctors = []
    for i in range(DOCTOR_COUNT):
        doctors.append(
            {
                "id": seed_id("doctor", i),
                "employee_code": f"{EMPLOYEE_PREFIX}{i:02d}",
                "full_name": f"{_DOCTOR_GIVEN[i]} {_DOCTOR_FAMILY[i]} {SEED_TAG}",
                # Index 0 is the unit head; the rest are doctors. No other
                # roles: RBAC is Phase 5 and this must not anticipate it.
                "role": "unit_head" if i == 0 else "doctor",
                # Every seeded user is active: the contract endpoint refuses an
                # inactive responsible doctor, so an inactive seed user would
                # only produce a confusing 422 in the gate.
                "is_active": True,
            }
        )

    await session.execute(
        insert(User)
        .values(doctors)
        .on_conflict_do_update(
            index_elements=[User.id],
            set_={
                "full_name": insert(User).excluded.full_name,
                "role": insert(User).excluded.role,
                "is_active": insert(User).excluded.is_active,
                "deleted_at": None,
            },
        )
    )
    counts["doctors"] = len(doctors)

    await session.execute(
        insert(Department)
        .values(
            [
                {
                    "id": department_id,
                    "code": DEPARTMENT_CODE,
                    "name": f"General Medicine {SEED_TAG}",
                    "unit_head_user_id": unit_head_id,
                    "active": True,
                }
            ]
        )
        .on_conflict_do_update(
            index_elements=[Department.id],
            set_={
                "name": insert(Department).excluded.name,
                "unit_head_user_id": insert(Department).excluded.unit_head_user_id,
                "active": True,
                "deleted_at": None,
            },
        )
    )
    counts["departments"] = 1

    # Now that the department exists, attach the doctors to it.
    await session.execute(
        User.__table__.update()
        .where(User.id.in_([d["id"] for d in doctors]))
        .values(department_id=department_id)
    )

    # ── patients ──────────────────────────────────────────────────
    patients = []
    for i in range(PATIENT_COUNT):
        patients.append(
            {
                "id": seed_id("patient", i),
                "mrn": f"{MRN_PREFIX}{1000 + i}",
                "name": f"{_GIVEN[i]} {_FAMILY[i]} {SEED_TAG}",
                # Deterministic dates of birth, spread across ages.
                "dob": dt.date(1950 + (i * 3) % 55, 1 + (i % 12), 1 + (i % 28)),
                "sex": ("F", "M")[i % 2],
                "phone_primary_e164": _phone(i),
                "preferred_language": ("en", "hi", "pa")[i % 3],
            }
        )

    await session.execute(
        insert(Patient)
        .values(patients)
        .on_conflict_do_update(
            index_elements=[Patient.id],
            set_={
                "name": insert(Patient).excluded.name,
                "mrn": insert(Patient).excluded.mrn,
                "dob": insert(Patient).excluded.dob,
                "sex": insert(Patient).excluded.sex,
                "phone_primary_e164": insert(Patient).excluded.phone_primary_e164,
                "deleted_at": None,
            },
        )
    )
    counts["patients"] = len(patients)

    # ── encounters: one per patient ───────────────────────────────
    encounters = []
    for i in range(PATIENT_COUNT):
        encounters.append(
            {
                "id": seed_id("encounter", i),
                "patient_id": seed_id("patient", i),
                "encounter_no": f"{ENCOUNTER_PREFIX}{1000 + i}",
                "type": _ENCOUNTER_TYPES[i % len(_ENCOUNTER_TYPES)],
                # Every seeded encounter is active and therefore dischargeable.
                # A seed full of already-discharged encounters gives a
                # developer nothing to exercise the gate with.
                "status": "active",
                "admitted_at": now - (24 + i * 6) * HOUR,
                "attending_doctor_id": seed_id("doctor", 1 + (i % (DOCTOR_COUNT - 1))),
                "department_id": department_id,
                "ward": _WARDS[i % len(_WARDS)],
                "bed": f"B{10 + i}",
            }
        )

    await session.execute(
        insert(Encounter)
        .values(encounters)
        .on_conflict_do_update(
            index_elements=[Encounter.id],
            set_={
                "status": insert(Encounter).excluded.status,
                "discharged_at": None,
                "ward": insert(Encounter).excluded.ward,
                "bed": insert(Encounter).excluded.bed,
                "attending_doctor_id": insert(Encounter).excluded.attending_doctor_id,
                "department_id": insert(Encounter).excluded.department_id,
                "deleted_at": None,
            },
        )
    )
    counts["encounters"] = len(encounters)

    # ── orders: 60, spread over the encounters, varied statuses ───
    orders = []
    for i in range(ORDER_COUNT):
        # Three orders land on each encounter (60 over 20). Both _TESTS and
        # _ORDER_STATUSES have 10 entries, so a plain `i % 10` would give every
        # encounter the same test in the same state three times over -- the one
        # thing the build plan explicitly asks this seed not to do ("60 orders
        # in varied states"). Offsetting by the round number breaks the cycle.
        round_no = i // PATIENT_COUNT
        variant = (i + round_no * 3) % len(_TESTS)
        code, name, category, tat = _TESTS[variant]
        orders.append(
            {
                "id": seed_id("order", i),
                "encounter_id": seed_id("encounter", i % PATIENT_COUNT),
                "patient_id": seed_id("patient", i % PATIENT_COUNT),
                "test_code": code,
                "test_name": name,
                "category": category,
                "status": _ORDER_STATUSES[variant],
                "ordered_at": now - (2 + i) * HOUR,
                "expected_tat_hours": tat,
                "ordered_by_user_id": seed_id("doctor", 1 + (i % (DOCTOR_COUNT - 1))),
            }
        )

    await session.execute(
        insert(Order)
        .values(orders)
        .on_conflict_do_update(
            index_elements=[Order.id],
            set_={
                "status": insert(Order).excluded.status,
                "test_name": insert(Order).excluded.test_name,
                "expected_tat_hours": insert(Order).excluded.expected_tat_hours,
                "deleted_at": None,
            },
        )
    )
    counts["orders"] = len(orders)

    # ── a few discharge medications, for Rule B to have something ─
    medications = []
    for i, (drug, atc, dose, route, freq, days, abx) in enumerate(_MEDICATIONS):
        medications.append(
            {
                "id": seed_id("medication", i),
                "encounter_id": seed_id("encounter", i),
                "drug_name": f"{drug} {SEED_TAG}",
                "atc_code": atc,
                "dose": dose,
                "route": route,
                "frequency": freq,
                "duration_days": days,
                "is_antibiotic": abx,
            }
        )

    await session.execute(
        insert(DischargeMedication)
        .values(medications)
        .on_conflict_do_update(
            index_elements=[DischargeMedication.id],
            set_={
                "dose": insert(DischargeMedication).excluded.dose,
                "frequency": insert(DischargeMedication).excluded.frequency,
                "deleted_at": None,
            },
        )
    )
    counts["medications"] = len(medications)

    return counts


async def _reset(session: AsyncSession) -> None:
    """Soft-delete a previous seed.

    **Soft**, never hard: clinical rows are never hard-deleted in this project
    and the seed script is not the place to make an exception to that rule.
    Only rows whose ids come from the seed namespace are touched, so a
    developer's own test data is left alone.
    """
    now = dt.datetime.now(dt.UTC)
    for model, kind, count in (
        (DischargeMedication, "medication", len(_MEDICATIONS)),
        (Order, "order", ORDER_COUNT),
        (Encounter, "encounter", PATIENT_COUNT),
        (Patient, "patient", PATIENT_COUNT),
        (User, "doctor", DOCTOR_COUNT),
    ):
        ids = [seed_id(kind, i) for i in range(count)]
        await session.execute(
            model.__table__.update()  # type: ignore[attr-defined]
            .where(model.id.in_(ids))  # type: ignore[attr-defined]
            .values(deleted_at=now)
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Soft-delete the previous seed before writing (never hard delete).",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_prod:
        print(
            "REFUSING: RG_ENV is prod. This script writes fake patients.",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with maker() as session:
            if args.reset:
                await _reset(session)
            counts = await _seed(session, dt.datetime.now(dt.UTC))
            await session.commit()

            live = (
                await session.execute(
                    select(func.count())
                    .select_from(Patient)
                    .where(
                        Patient.mrn.like(f"{MRN_PREFIX}%"),
                        Patient.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    for label, count in counts.items():
        print(f"  {label:<14} {count}")
    print(f"\nSeeded. {live} seed patients are live. Every row is marked {SEED_TAG}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
