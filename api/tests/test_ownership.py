"""Phase 4.1 / 4.2 — owner resolution, availability and reassignment.

The six-step fallthrough is the whole of 4.2, and the tests below walk it one
step at a time by making each earlier step unavailable in turn. That shape is
deliberate: asserting "step 3 works" while steps 1 and 2 also match would pass
even if the ordering were wrong.

Two properties get the most attention because they are the ones that fail
quietly:

* **Resolution never returns nobody.** A flag routed to no one is silence.
* **A roster row for an absent doctor is not honoured.** That is the exact
  *"the system will confidently notify someone who left"* failure ADR 0004
  names, and a roster that looks populated while resolving nobody is worse
  than an empty one.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.services.ownership import (
    STEP_ADMIN_FALLBACK,
    STEP_CONTRACT_DOCTOR,
    STEP_DELEGATE,
    STEP_ROSTER_BACKUP,
    STEP_ROSTER_PRIMARY,
    STEP_UNIT_HEAD,
    CaseNotFoundError,
    ReassignmentReasonRequiredError,
    assign_resolved_owner,
    departments_with_stale_roster,
    fallthrough_metrics,
    is_available,
    reassign_case,
    resolve_owner,
)

pytestmark = pytest.mark.integration

HOUR = dt.timedelta(hours=1)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        conn = await engine.connect()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    trans = await conn.begin()
    maker = async_sessionmaker(
        bind=conn,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with maker() as s:
            yield s
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _world(session: AsyncSession) -> dict[str, Any]:
    """A department, a unit head, a contracted doctor, cover, and a case.

    Everyone starts available. Each test makes exactly the people it needs
    unavailable, so the step under test is the first one that can match.
    """
    tag = uuid.uuid4().hex[:8]
    ids = {
        k: str(uuid.uuid4())
        for k in (
            "dept",
            "doctor",
            "delegate",
            "primary",
            "backup",
            "head",
            "admin",
            "pat",
            "enc",
            "order",
            "contract",
            "case",
        )
    }
    for key, role, code in (
        ("doctor", "doctor", "DOC"),
        ("delegate", "doctor", "DEL"),
        ("primary", "doctor", "PRI"),
        ("backup", "doctor", "BAK"),
        ("head", "unit_head", "HED"),
        ("admin", "admin", "ADM"),
    ):
        await session.execute(
            text(
                "INSERT INTO users (id, employee_code, full_name, role, is_active) "
                "VALUES (:i, :c, :n, :r, true)"
            ),
            {"i": ids[key], "c": f"{code}{tag}", "n": f"Dr {code} {tag}", "r": role},
        )
    await session.execute(
        text(
            "INSERT INTO departments (id, code, name, unit_head_user_id, active) "
            "VALUES (:i, :c, 'Medicine', :h, true)"
        ),
        {"i": ids["dept"], "c": f"D{tag}", "h": ids["head"]},
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"E{tag}", "d": ids["dept"]},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'URC', 'Urine Culture', 'micro', now(), 'in_lab')"
        ),
        {"i": ids["order"], "e": ids["enc"], "p": ids["pat"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["contract"], "e": ids["enc"], "o": ids["order"], "u": ids["doctor"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, state, severity, "
            " flagged_at) "
            "VALUES (:i, :o, :e, :p, :c, 'flagged', 'critical', now())"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["contract"],
        },
    )
    await session.commit()
    return ids


async def _absent(
    session: AsyncSession,
    user_id: str,
    *,
    absence_type: str = "leave",
    delegate: str | None = None,
    ends_at: dt.datetime | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO user_absences "
            "(id, user_id, absence_type, starts_at, ends_at, delegate_user_id) "
            "VALUES (:i, :u, :t, now() - interval '1 day', :e, :d)"
        ),
        {
            "i": str(uuid.uuid4()),
            "u": user_id,
            "t": absence_type,
            "e": ends_at,
            "d": delegate,
        },
    )


async def _roster(
    session: AsyncSession,
    *,
    user_id: str,
    department_id: str,
    role: str = "primary",
    covering_now: bool = True,
) -> None:
    start = dt.datetime.now(dt.UTC) - (HOUR if covering_now else 10 * HOUR)
    end = dt.datetime.now(dt.UTC) + (HOUR if covering_now else -9 * HOUR)
    await session.execute(
        text(
            "INSERT INTO duty_roster "
            "(id, user_id, department_id, shift_start, shift_end, role_on_duty) "
            "VALUES (:i, :u, :d, :s, :e, :r)"
        ),
        {
            "i": str(uuid.uuid4()),
            "u": user_id,
            "d": department_id,
            "s": start,
            "e": end,
            "r": role,
        },
    )


# ── availability ──────────────────────────────────────────────────────


async def test_an_active_user_with_no_absence_is_available(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    now = dt.datetime.now(dt.UTC)
    assert await is_available(session, uuid.UUID(ids["doctor"]), now)


async def test_a_user_on_leave_is_not_available(session: AsyncSession) -> None:
    ids = await _world(session)
    await _absent(session, ids["doctor"], ends_at=dt.datetime.now(dt.UTC) + HOUR)
    assert not await is_available(
        session, uuid.UUID(ids["doctor"]), dt.datetime.now(dt.UTC)
    )


async def test_an_absence_with_no_end_date_is_indefinite(
    session: AsyncSession,
) -> None:
    """*"ends_at (null = indefinite)"*. A resigned doctor with an end date
    would come back on duty by arithmetic."""
    ids = await _world(session)
    await _absent(session, ids["doctor"], absence_type="resigned", ends_at=None)
    far_future = dt.datetime.now(dt.UTC) + dt.timedelta(days=3650)
    assert not await is_available(session, uuid.UUID(ids["doctor"]), far_future)


async def test_an_expired_absence_does_not_block(session: AsyncSession) -> None:
    ids = await _world(session)
    await _absent(session, ids["doctor"], ends_at=dt.datetime.now(dt.UTC) - HOUR)
    now = dt.datetime.now(dt.UTC)
    assert await is_available(session, uuid.UUID(ids["doctor"]), now)


async def test_a_deactivated_user_is_not_available(session: AsyncSession) -> None:
    ids = await _world(session)
    await session.execute(
        text("UPDATE users SET is_active = false WHERE id = :u"), {"u": ids["doctor"]}
    )
    assert not await is_available(
        session, uuid.UUID(ids["doctor"]), dt.datetime.now(dt.UTC)
    )


# ── the six steps, in order ───────────────────────────────────────────


async def test_step_1_the_contract_doctor_when_available(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_CONTRACT_DOCTOR
    assert resolution.level == 1
    assert str(resolution.user_id) == ids["doctor"]
    assert resolution.is_fallthrough is False


async def test_step_2_the_delegate_when_the_doctor_is_absent(
    session: AsyncSession,
) -> None:
    """*"Owner on leave → delegate notified → event logged."* (4.7)"""
    ids = await _world(session)
    await _absent(session, ids["doctor"], delegate=ids["delegate"])

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_DELEGATE
    assert resolution.level == 2
    assert str(resolution.user_id) == ids["delegate"]

    events = await _events(session, ids["case"])
    assert "owner_resolved" in events


async def test_step_3_the_roster_primary(session: AsyncSession) -> None:
    ids = await _world(session)
    await _absent(session, ids["doctor"])  # no delegate
    await _roster(session, user_id=ids["primary"], department_id=ids["dept"])

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_ROSTER_PRIMARY
    assert str(resolution.user_id) == ids["primary"]


async def test_step_4_the_roster_backup_when_no_primary_is_on_shift(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    await _absent(session, ids["doctor"])
    await _roster(
        session, user_id=ids["backup"], department_id=ids["dept"], role="backup"
    )

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_ROSTER_BACKUP
    assert str(resolution.user_id) == ids["backup"]


async def test_step_5_the_unit_head_when_the_roster_yields_nobody(
    session: AsyncSession,
) -> None:
    """ADR 0004's *"graceful fallthrough"*: a stale roster degrades to the
    person responsible for maintaining it, not to silence."""
    ids = await _world(session)
    await _absent(session, ids["doctor"])

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_UNIT_HEAD
    assert str(resolution.user_id) == ids["head"]
    assert resolution.is_fallthrough is True


async def test_step_6_the_admin_fallback(session: AsyncSession) -> None:
    ids = await _world(session)
    await _absent(session, ids["doctor"])
    await _absent(session, ids["head"])

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_ADMIN_FALLBACK
    assert str(resolution.user_id) == ids["admin"]
    assert resolution.unresolved is False
    assert resolution.is_fallthrough is True


async def test_resolution_never_returns_silently(session: AsyncSession) -> None:
    """With literally nobody available, the result is a recorded
    ``unresolved`` rather than an exception or a quiet None. The case keeps
    its severity, its timers and its events either way.

    ⚠️ **Every** admin has to be absent, not only this world's. The final
    fallback step looks for *any* administrator, so the test's own admin
    being on leave proves nothing while some other admin row exists — and one
    always will in a real database, which is how this quietly started passing
    for the wrong reason and then failed the moment a dev login was seeded.
    """
    ids = await _world(session)
    for key in ("doctor", "head", "admin"):
        await _absent(session, ids[key])

    others = (
        await session.execute(
            text(
                "SELECT id FROM users"
                " WHERE role = 'admin' AND is_active AND deleted_at IS NULL"
                "   AND id <> CAST(:mine AS uuid)"
            ),
            {"mine": ids["admin"]},
        )
    ).scalars()
    for other in others:
        await _absent(session, str(other))

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.user_id is None
    assert resolution.unresolved is True
    assert "owner_unresolvable" in await _events(session, ids["case"])


# ── the failure ADR 0004 names ────────────────────────────────────────


async def test_a_roster_row_for_an_absent_doctor_is_not_honoured(
    session: AsyncSession,
) -> None:
    """*"The system will confidently notify someone who left."*

    A roster row that names a doctor who is simultaneously on leave is stale
    data. Honouring it would route a critical flag to someone who cannot act,
    and the ladder would tick against a person who will never acknowledge.
    """
    ids = await _world(session)
    await _absent(session, ids["doctor"])
    await _roster(session, user_id=ids["primary"], department_id=ids["dept"])
    await _absent(session, ids["primary"])  # rostered, but on leave

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert str(resolution.user_id) != ids["primary"]
    assert resolution.step == STEP_UNIT_HEAD


async def test_a_shift_that_does_not_cover_now_is_not_used(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    await _absent(session, ids["doctor"])
    await _roster(
        session, user_id=ids["primary"], department_id=ids["dept"], covering_now=False
    )

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_UNIT_HEAD


async def test_a_delegate_who_is_also_absent_is_skipped(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    await _absent(session, ids["doctor"], delegate=ids["delegate"])
    await _absent(session, ids["delegate"])

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]))
    assert resolution.step == STEP_UNIT_HEAD


async def test_resolution_is_deterministic_across_repeated_calls(
    session: AsyncSession,
) -> None:
    """Two rostered primaries on overlapping shifts must not resolve to
    different people on different days."""
    ids = await _world(session)
    await _absent(session, ids["doctor"])
    await _roster(session, user_id=ids["primary"], department_id=ids["dept"])
    await _roster(session, user_id=ids["backup"], department_id=ids["dept"])

    seen = set()
    for _ in range(4):
        resolution = await resolve_owner(session, uuid.UUID(ids["case"]), record=False)
        seen.add(str(resolution.user_id))
    assert len(seen) == 1


# ── every hop writes an event ─────────────────────────────────────────


async def test_every_resolution_records_its_reason(session: AsyncSession) -> None:
    """*"Each hop writes a ``case_event`` with the reason."*"""
    ids = await _world(session)
    await _absent(session, ids["doctor"], delegate=ids["delegate"])
    await resolve_owner(session, uuid.UUID(ids["case"]))

    row = (
        await session.execute(
            text(
                "SELECT payload FROM case_events "
                " WHERE case_id = :c AND event_type = 'owner_resolved'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert row.payload["step"] == STEP_DELEGATE
    assert row.payload["level"] == 2
    assert row.payload["reason"]
    assert row.payload["roster_fallthrough"] is False


async def test_a_read_only_resolution_writes_no_event(
    session: AsyncSession,
) -> None:
    """The dashboard asks "who would this go to?" — answering must not append
    to an append-only log."""
    ids = await _world(session)
    before = len(await _events(session, ids["case"]))
    await resolve_owner(session, uuid.UUID(ids["case"]), record=False)
    assert len(await _events(session, ids["case"])) == before


# ── assignment ────────────────────────────────────────────────────────


async def test_assigning_writes_the_owner_onto_the_case(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    await assign_resolved_owner(session, uuid.UUID(ids["case"]))
    row = (
        await session.execute(
            text("SELECT current_owner_id FROM pending_cases WHERE id = :c"),
            {"c": ids["case"]},
        )
    ).one()
    assert str(row.current_owner_id) == ids["doctor"]


async def test_assignment_does_not_reown_an_acknowledged_case(
    session: AsyncSession,
) -> None:
    """Re-owning a case somebody already dealt with would put it back in their
    queue."""
    ids = await _world(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'acknowledged', "
            "       acknowledged_at = now(), current_owner_id = NULL WHERE id = :c"
        ),
        {"c": ids["case"]},
    )
    await assign_resolved_owner(session, uuid.UUID(ids["case"]))
    row = (
        await session.execute(
            text("SELECT current_owner_id FROM pending_cases WHERE id = :c"),
            {"c": ids["case"]},
        )
    ).one()
    assert row.current_owner_id is None


# ── reassignment ──────────────────────────────────────────────────────


async def test_reassignment_requires_a_reason(session: AsyncSession) -> None:
    ids = await _world(session)
    for blank in ("", "   ", "\n\t "):
        with pytest.raises(ReassignmentReasonRequiredError):
            await reassign_case(
                session,
                uuid.UUID(ids["case"]),
                new_owner_id=uuid.UUID(ids["backup"]),
                reason=blank,
            )


async def test_reassignment_moves_the_owner_and_records_why(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    await assign_resolved_owner(session, uuid.UUID(ids["case"]))

    result = await reassign_case(
        session,
        uuid.UUID(ids["case"]),
        new_owner_id=uuid.UUID(ids["backup"]),
        reason="I am off shift at 18:00 and Dr BAK is covering.",
    )
    assert str(result.previous_owner_id) == ids["doctor"]
    assert str(result.new_owner_id) == ids["backup"]

    row = (
        await session.execute(
            text(
                "SELECT payload FROM case_events "
                " WHERE case_id = :c AND event_type = 'case_reassigned'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert row.payload["reason"].startswith("I am off shift")
    assert row.payload["escalation_clock_reset"] is False


async def test_reassignment_of_an_unknown_case_raises(
    session: AsyncSession,
) -> None:
    with pytest.raises(CaseNotFoundError):
        await reassign_case(
            session,
            uuid.uuid4(),
            new_owner_id=uuid.uuid4(),
            reason="anything",
        )


# ── ADR 0004's staleness guards ───────────────────────────────────────


async def test_a_department_with_no_covering_shift_is_reported_stale(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    stale = await departments_with_stale_roster(session)
    assert ids["dept"] in {d["department_id"] for d in stale}


async def test_a_department_with_a_covering_shift_is_not_stale(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    await _roster(session, user_id=ids["primary"], department_id=ids["dept"])
    stale = await departments_with_stale_roster(session)
    assert ids["dept"] not in {d["department_id"] for d in stale}


async def test_fallthroughs_are_counted_per_department(
    session: AsyncSession,
) -> None:
    """*"A rising number is the measurable signature of a roster going
    stale."*"""
    ids = await _world(session)
    await _absent(session, ids["doctor"])
    await resolve_owner(session, uuid.UUID(ids["case"]))

    metrics = {m["department_id"]: m for m in await fallthrough_metrics(session)}
    assert metrics[ids["dept"]]["fell_through_to_unit_head_or_admin"] >= 1


async def _events(session: AsyncSession, case_id: str) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT event_type FROM case_events WHERE case_id = :c "
                " ORDER BY occurred_at, event_type"
            ),
            {"c": case_id},
        )
    ).all()
    return [r.event_type for r in rows]
