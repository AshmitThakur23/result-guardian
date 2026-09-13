"""Phase 5.5 ★ — the hash-chained audit log.

The claim being tested is narrow and strong: **you cannot change a row in
``audit_log`` without it being detectable.** That breaks into four things, and
each is tested against a real PostgreSQL rather than a mock, because three of
the four are enforced by the database and a mock would agree with whatever the
code believes.

1. Canonical JSON is reproducible — same content, same hash, whatever the key
   order, the timezone or the encoding.
2. The chain links, and ``verify_chain`` recomputes it.
3. Tampering is caught, and the three kinds of tampering are distinguished.
4. The append-only trigger actually refuses UPDATE and DELETE.

There is also an honest negative result recorded here about the ``REVOKE``
half of the plan's DB rules. See ``test_revoke_is_ineffective_against_a_superuser``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import itertools
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.services import audit
from tests._phase5 import build_world

pytestmark = pytest.mark.integration


# ── 1. canonical JSON ──────────────────────────────────────────────────


def test_key_order_does_not_change_the_json() -> None:
    """*"Canonical JSON: sorted keys, no whitespace, UTC ISO-8601."*"""
    assert audit.canonical_json({"b": 2, "a": 1}) == audit.canonical_json(
        {"a": 1, "b": 2}
    )
    assert audit.canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_nested_keys_are_sorted_too() -> None:
    left = {"outer": {"z": 1, "a": {"q": 1, "b": 2}}}
    right = {"outer": {"a": {"b": 2, "q": 1}, "z": 1}}
    assert audit.canonical_json(left) == audit.canonical_json(right)


def test_there_is_no_whitespace() -> None:
    rendered = audit.canonical_json({"a": [1, 2], "b": {"c": 3}})
    assert " " not in rendered
    assert rendered == '{"a":[1,2],"b":{"c":3}}'


def test_the_same_instant_in_two_zones_hashes_the_same() -> None:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    same_moment_ist = dt.datetime(2026, 9, 13, 15, 30, tzinfo=ist)
    same_moment_utc = dt.datetime(2026, 9, 13, 10, 0, tzinfo=dt.UTC)
    assert audit.canonical_json({"t": same_moment_ist}) == audit.canonical_json(
        {"t": same_moment_utc}
    )


def test_a_decimal_keeps_its_exact_value() -> None:
    """A float round-trip would change the hash on a different platform."""
    rendered = audit.canonical_json({"v": decimal.Decimal("0.1")})
    assert rendered == '{"v":"0.1"}'
    assert json.loads(rendered)["v"] == "0.1"


def test_non_ascii_is_not_escaped() -> None:
    """A Hindi note must hash the same whether or not escaping happens."""
    rendered = audit.canonical_json({"note": "मरीज़ को बुलाया"})
    assert "\\u" not in rendered
    assert "मरीज़" in rendered


def test_the_field_separator_prevents_a_collision() -> None:
    """Without a separator, ("ab","c") and ("a","bc") would hash identically."""
    common: dict[str, Any] = {
        "seq": 1,
        "occurred_at": dt.datetime(2026, 9, 13, tzinfo=dt.UTC),
        "actor_user_id": None,
        "before": None,
        "after": None,
        "prev_hash": audit.GENESIS_HASH,
    }
    left = audit.compute_row_hash(action="ab", entity_type="c", entity_id="x", **common)
    right = audit.compute_row_hash(
        action="a", entity_type="bc", entity_id="x", **common
    )
    assert left != right


def test_the_hash_is_stable_across_calls() -> None:
    args: dict[str, Any] = {
        "seq": 7,
        "occurred_at": dt.datetime(2026, 9, 13, 10, 0, tzinfo=dt.UTC),
        "actor_user_id": uuid.UUID("00000000-0000-0000-0000-0000000000ab"),
        "action": "case.closed",
        "entity_type": "pending_case",
        "entity_id": "abc",
        "before": {"state": "flagged"},
        "after": {"state": "closed", "note": "done"},
        "prev_hash": "a" * 64,
    }
    assert audit.compute_row_hash(**args) == audit.compute_row_hash(**args)
    # And it is a SHA-256 hex digest, which the column's CHECK also enforces.
    assert len(audit.compute_row_hash(**args)) == 64


# ── 2. the chain links ─────────────────────────────────────────────────


async def _append(session: AsyncSession, n: int, ids: dict[str, str]) -> list[int]:
    seqs = []
    for i in range(n):
        row = await audit.append(
            session,
            action="case.note_added",
            entity_type="pending_case",
            entity_id=ids["case"],
            actor_user_id=uuid.UUID(ids["doctor"]),
            after={"note": f"entry {i}", "index": i},
        )
        seqs.append(row.seq)
    await session.commit()
    return seqs


@pytest.mark.asyncio
async def test_the_first_row_starts_from_genesis(session: AsyncSession) -> None:
    ids = await build_world(session)
    existing = (
        await session.execute(text("SELECT count(*) FROM audit_log"))
    ).scalar_one()

    row = await audit.append(
        session,
        action="case.note_added",
        entity_type="pending_case",
        entity_id=ids["case"],
        after={"note": "first"},
    )
    await session.commit()

    if existing == 0:
        assert row.prev_hash == audit.GENESIS_HASH
    assert row.seq >= 1


@pytest.mark.asyncio
async def test_each_row_carries_the_previous_rows_hash(session: AsyncSession) -> None:
    ids = await build_world(session)
    seqs = await _append(session, 4, ids)

    rows = (
        await session.execute(
            text(
                "SELECT seq, prev_hash, row_hash FROM audit_log "
                " WHERE seq = ANY(:s) ORDER BY seq"
            ),
            {"s": seqs},
        )
    ).all()
    for earlier, later in itertools.pairwise(rows):
        assert later.prev_hash == earlier.row_hash


@pytest.mark.asyncio
async def test_verify_chain_recomputes_and_finds_it_intact(
    session: AsyncSession,
) -> None:
    ids = await build_world(session)
    await _append(session, 5, ids)

    result = await audit.verify_chain(session)
    assert result.intact is True, result.first_break_reason
    assert result.rows_checked >= 5
    assert result.head_hash is not None


@pytest.mark.asyncio
async def test_seq_is_gapless_and_monotonic(session: AsyncSession) -> None:
    """``seq`` is what makes "recompute the chain" well-defined."""
    ids = await build_world(session)
    seqs = await _append(session, 6, ids)
    assert seqs == sorted(seqs)
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


# ── 3. tampering is detected ───────────────────────────────────────────
#
# These tests have to defeat the append-only trigger to set up the tamper,
# which is itself proof the trigger is doing something. They use
# `session_replication_role = replica`, which disables triggers for the
# session -- the same mechanism the project's test cleanup already uses.


async def _tamper(session: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    await session.execute(text(sql), params)
    await session.execute(text("SET LOCAL session_replication_role = origin"))


@pytest.mark.asyncio
async def test_editing_a_rows_content_breaks_its_own_hash(
    session: AsyncSession,
) -> None:
    """The single most important assertion in Phase 5."""
    ids = await build_world(session)
    seqs = await _append(session, 4, ids)
    target = seqs[1]

    assert (await audit.verify_chain(session)).intact is True

    await _tamper(
        session,
        "UPDATE audit_log SET after = CAST(:a AS jsonb) WHERE seq = :s",
        {"a": '{"note":"quietly rewritten"}', "s": target},
    )

    result = await audit.verify_chain(session)
    assert result.intact is False
    assert result.first_break_seq == target
    assert "row_hash does not match" in (result.first_break_reason or "")


@pytest.mark.asyncio
async def test_deleting_a_row_leaves_a_seq_gap(session: AsyncSession) -> None:
    ids = await build_world(session)
    seqs = await _append(session, 4, ids)
    target = seqs[1]

    await _tamper(session, "DELETE FROM audit_log WHERE seq = :s", {"s": target})

    result = await audit.verify_chain(session)
    assert result.intact is False
    assert "seq gap" in (result.first_break_reason or "")


@pytest.mark.asyncio
async def test_relinking_after_a_deletion_still_breaks_prev_hash(
    session: AsyncSession,
) -> None:
    """The sophisticated attack: delete a row *and* renumber to hide the gap.

    An attacker who knows about the seq check will close it. They cannot close
    the linkage: the row that followed the deleted one still carries the
    deleted row's hash as its ``prev_hash``.
    """
    ids = await build_world(session)
    seqs = await _append(session, 5, ids)
    victim, follower = seqs[2], seqs[3]

    await _tamper(session, "DELETE FROM audit_log WHERE seq = :s", {"s": victim})
    await _tamper(
        session,
        "UPDATE audit_log SET seq = seq - 1 WHERE seq > :s",
        {"s": victim},
    )

    result = await audit.verify_chain(session)
    assert result.intact is False
    assert "prev_hash does not match" in (result.first_break_reason or "")
    assert result.first_break_seq == follower - 1


@pytest.mark.asyncio
async def test_an_intact_chain_stays_intact_after_the_tamper_is_reverted(
    session: AsyncSession,
) -> None:
    """Guards against the verifier reporting a break on everything."""
    ids = await build_world(session)
    seqs = await _append(session, 3, ids)
    original = (
        await session.execute(
            text("SELECT after FROM audit_log WHERE seq = :s"), {"s": seqs[1]}
        )
    ).scalar_one()

    await _tamper(
        session,
        "UPDATE audit_log SET after = CAST(:a AS jsonb) WHERE seq = :s",
        {"a": '{"note":"tampered"}', "s": seqs[1]},
    )
    assert (await audit.verify_chain(session)).intact is False

    await _tamper(
        session,
        "UPDATE audit_log SET after = CAST(:a AS jsonb) WHERE seq = :s",
        {"a": json.dumps(original), "s": seqs[1]},
    )
    assert (await audit.verify_chain(session)).intact is True


# ── 4. the database refuses to let it happen at all ────────────────────


@pytest.mark.asyncio
async def test_update_on_audit_log_is_rejected_by_the_trigger(
    session: AsyncSession,
) -> None:
    """*"DB rules: … ``BEFORE UPDATE/DELETE`` trigger raises exception."*"""
    ids = await build_world(session)
    seqs = await _append(session, 1, ids)

    with pytest.raises(DBAPIError) as caught:
        await session.execute(
            text("UPDATE audit_log SET action = 'x' WHERE seq = :s"), {"s": seqs[0]}
        )
    assert "append-only" in str(caught.value)
    await session.rollback()


@pytest.mark.asyncio
async def test_delete_on_audit_log_is_rejected_by_the_trigger(
    session: AsyncSession,
) -> None:
    ids = await build_world(session)
    seqs = await _append(session, 1, ids)

    with pytest.raises(DBAPIError) as caught:
        await session.execute(
            text("DELETE FROM audit_log WHERE seq = :s"), {"s": seqs[0]}
        )
    assert "append-only" in str(caught.value)
    await session.rollback()


@pytest.mark.asyncio
async def test_the_anchors_table_is_append_only_too(session: AsyncSession) -> None:
    await audit.anchor_chain(session)
    await session.commit()

    with pytest.raises(DBAPIError):
        await session.execute(text("UPDATE audit_anchors SET chain_intact = false"))
    await session.rollback()


@pytest.mark.asyncio
async def test_revoke_is_ineffective_against_a_superuser(
    session: AsyncSession,
) -> None:
    """**An honest negative result, recorded rather than papered over.**

    The plan asks for two DB rules: *"``REVOKE UPDATE, DELETE`` from the app
    role; ``BEFORE UPDATE/DELETE`` trigger raises exception."* The trigger
    works — the two tests above prove it. The REVOKE was applied correctly and
    the ACL shows it (``arxt``: INSERT, SELECT, REFERENCES, TRIGGER — **no
    UPDATE, no DELETE**), but ``rg_app`` is the bootstrap **superuser**, and
    PostgreSQL superusers bypass every privilege check.

    So layer 2 of the three-layer defence is currently inert on this
    deployment. Layers 1 (trigger) and 3 (hash chain) are unaffected and are
    the ones that actually resist a determined attacker anyway — a separate
    non-superuser application role is Phase 10 security-hardening work.

    This test asserts the *situation*, so that the day the app stops running
    as a superuser, it fails and someone re-reads this docstring.
    """
    row = (
        await session.execute(
            text(
                "SELECT rolsuper, "
                "       has_table_privilege(current_user,'audit_log','UPDATE') AS upd, "
                "       has_table_privilege("
                "         current_user, 'audit_log', 'DELETE') AS can_delete, "
                "       (SELECT relacl::text FROM pg_class "
                "         WHERE relname = 'audit_log') AS acl "
                "  FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()

    # The grant itself is correct: no 'w' (UPDATE) and no 'd' (DELETE).
    assert "w" not in (row.acl or "").split("=")[-1].split("/")[0]
    assert "d" not in (row.acl or "").split("=")[-1].split("/")[0]

    if row.rolsuper:
        # Superuser: the REVOKE cannot bite, and has_table_privilege says so.
        assert row.upd is True
        assert row.can_delete is True
    else:
        # A non-superuser role -- the REVOKE is live and layer 2 is real.
        assert row.upd is False
        assert row.can_delete is False


# ── concurrency: one writer, one chain ─────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_appends_do_not_fork_the_chain() -> None:
    """``rg_audit_next_seq()``'s advisory lock, under real contention.

    Four independent connections, not four sessions on one connection: an
    advisory lock is held per-session, so testing it on a shared connection
    would prove nothing. Each writer commits, so the rows survive and are
    cleaned up explicitly at the end -- the append-only trigger means the
    cleanup has to disable triggers, which is itself worth exercising.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_size=6)
    try:
        async with engine.connect() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    marker = f"concurrency-{uuid.uuid4().hex[:8]}"

    async def writer(index: int) -> None:
        async with maker() as s:
            for attempt in range(3):
                await audit.append(
                    s,
                    action="config.changed",
                    entity_type="user",
                    entity_id=marker,
                    after={"writer": index, "attempt": attempt},
                )
                await s.commit()

    try:
        await asyncio.gather(*(writer(i) for i in range(4)))

        async with maker() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT seq, prev_hash, row_hash FROM audit_log "
                        " WHERE entity_id = :m ORDER BY seq"
                    ),
                    {"m": marker},
                )
            ).all()

            assert len(rows) == 12, "every append must land"
            seqs = [int(r.seq) for r in rows]
            assert len(set(seqs)) == 12, "no two rows may share a seq"

            # The 12 rows are contiguous within themselves, and the whole
            # chain still verifies -- which is the real assertion.
            verification = await audit.verify_chain(s)
            assert verification.intact is True, verification.first_break_reason
    finally:
        async with maker() as s:
            await s.execute(text("SET session_replication_role = replica"))
            await s.execute(
                text("DELETE FROM audit_log WHERE entity_id = :m"), {"m": marker}
            )
            await s.execute(text("SET session_replication_role = origin"))
            await s.commit()
        await engine.dispose()


# ── the API surface ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_endpoint_reports_a_broken_chain_with_200(
    client: Any, session: AsyncSession
) -> None:
    """A broken chain is an **answer**, not a 500.

    A monitoring system must be able to tell "the verifier found tampering"
    from "the verifier is down", and an error status conflates them.
    """
    from tests._phase5 import bearer

    ids = await build_world(session)
    seqs = await _append(session, 3, ids)
    headers = await bearer(client, ids, "auditor")

    good = await client.get("/api/audit/verify", headers=headers)
    assert good.status_code == 200
    assert good.json()["intact"] is True

    await _tamper(
        session,
        "UPDATE audit_log SET after = CAST(:a AS jsonb) WHERE seq = :s",
        {"a": '{"note":"changed"}', "s": seqs[1]},
    )

    bad = await client.get("/api/audit/verify", headers=headers)
    assert bad.status_code == 200
    body = bad.json()
    assert body["intact"] is False
    assert body["first_break_seq"] == seqs[1]


@pytest.mark.asyncio
async def test_a_doctor_cannot_read_the_audit_trail(
    client: Any, session: AsyncSession
) -> None:
    """The log spans every department. No clinical role has a reason for it."""
    from tests._phase5 import bearer

    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.get("/api/audit", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_csv_export_carries_the_hashes(
    client: Any, session: AsyncSession
) -> None:
    """Without them the export is claims; with them it is evidence."""
    from tests._phase5 import bearer

    ids = await build_world(session)
    await _append(session, 2, ids)
    headers = await bearer(client, ids, "auditor")

    response = await client.get("/api/audit/export.csv", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    header_line = response.text.splitlines()[0]
    assert "prev_hash" in header_line
    assert "row_hash" in header_line


@pytest.mark.asyncio
async def test_csv_export_neutralises_a_formula(
    client: Any, session: AsyncSession
) -> None:
    """A break-glass reason beginning ``=`` must not execute in Excel.

    A *plain string* cell is the case that matters. ``before``/``after`` are
    rendered as JSON and therefore always start with ``{`` or ``[``, which
    Excel treats as text whatever follows; ``break_glass_reason``,
    ``actor_name`` and ``action`` are the free-text cells an attacker can
    actually start with ``=``.
    """
    from tests._phase5 import bearer

    ids = await build_world(session)
    await audit.append(
        session,
        action=audit.ACTION_BREAK_GLASS,
        entity_type="pending_case",
        entity_id=ids["case"],
        break_glass_reason="=cmd|'/c calc'!A1",
    )
    await session.commit()

    headers = await bearer(client, ids, "auditor")
    response = await client.get(
        "/api/audit/export.csv",
        params={"entity_id": ids["case"]},
        headers=headers,
    )
    assert response.status_code == 200
    # Prefixed with an apostrophe, so the cell is read as text, not a formula.
    assert "'=cmd" in response.text
    # And the dangerous form -- a bare '=' opening the cell -- is absent.
    assert ",=cmd" not in response.text


@pytest.mark.asyncio
async def test_break_glass_rows_are_filterable(
    client: Any, session: AsyncSession
) -> None:
    """*"logged loudly"* — one query, not a scan of every row."""
    from tests._phase5 import bearer

    ids = await build_world(session)
    await audit.append(
        session,
        action=audit.ACTION_BREAK_GLASS,
        entity_type="user",
        entity_id=ids["doctor"],
        actor_user_id=uuid.UUID(ids["doctor"]),
        break_glass_reason="Covering night duty for Surgery, patient recalled",
    )
    await _append(session, 2, ids)

    headers = await bearer(client, ids, "auditor")
    response = await client.get(
        "/api/audit", params={"break_glass_only": True}, headers=headers
    )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert rows, "the break-glass row must be findable"
    assert all(r["break_glass_reason"] for r in rows)


# ── the nightly notarisation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_anchoring_publishes_the_head_hash(session: AsyncSession) -> None:
    ids = await build_world(session)
    await _append(session, 3, ids)

    result = await audit.anchor_chain(session)
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT head_seq, head_hash, chain_intact, first_break_seq "
                "  FROM audit_anchors ORDER BY anchored_at DESC LIMIT 1"
            )
        )
    ).one()
    assert row.chain_intact is True
    assert row.first_break_seq is None
    assert row.head_hash == result.head_hash


@pytest.mark.asyncio
async def test_the_sql_verifier_agrees_about_an_intact_chain(
    session: AsyncSession,
) -> None:
    """``rg_verify_audit_chain()`` runs from pg_cron when the API is stopped."""
    ids = await build_world(session)
    await _append(session, 3, ids)

    row = (await session.execute(text("SELECT * FROM rg_verify_audit_chain()"))).one()
    assert row.intact is True
    python_result = await audit.verify_chain(session)
    assert int(row.head_seq) == python_result.head_seq


@pytest.mark.asyncio
async def test_the_sql_verifier_catches_a_deletion(session: AsyncSession) -> None:
    """The half the SQL function is responsible for: structural tampering."""
    ids = await build_world(session)
    seqs = await _append(session, 4, ids)

    await _tamper(session, "DELETE FROM audit_log WHERE seq = :s", {"s": seqs[1]})

    row = (await session.execute(text("SELECT * FROM rg_verify_audit_chain()"))).one()
    assert row.intact is False
    assert int(row.first_break_seq) == seqs[2]


@pytest.mark.asyncio
async def test_an_anchor_records_a_break_rather_than_hiding_it(
    session: AsyncSession,
) -> None:
    ids = await build_world(session)
    seqs = await _append(session, 3, ids)
    await _tamper(
        session,
        "UPDATE audit_log SET after = CAST(:a AS jsonb) WHERE seq = :s",
        {"a": '{"x":1}', "s": seqs[1]},
    )

    result = await audit.anchor_chain(session)
    await session.commit()
    assert result.intact is False

    row = (
        await session.execute(
            text(
                "SELECT chain_intact, first_break_seq FROM audit_anchors "
                " ORDER BY anchored_at DESC LIMIT 1"
            )
        )
    ).one()
    assert row.chain_intact is False
    assert int(row.first_break_seq) == seqs[1]


@pytest.mark.asyncio
async def test_the_nightly_cron_job_is_scheduled(session: AsyncSession) -> None:
    row = (
        await session.execute(
            text(
                "SELECT schedule, command FROM cron.job "
                " WHERE jobname = 'rg_anchor_audit_chain'"
            )
        )
    ).first()
    if row is None:
        pytest.skip("pg_cron not installed in this database")
    # 21:00 UTC = 02:30 IST.
    assert row.schedule == "0 21 * * *"
    assert "rg_anchor_audit_chain" in row.command


# ── same transaction as the change ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rolled_back_change_leaves_no_audit_row(
    session: AsyncSession,
) -> None:
    """*"Writes happen in the same transaction as the change — never
    fire-and-forget."*

    The property that makes the log trustworthy in both directions: no row
    claiming something that did not happen, and nothing happening unlogged.
    """
    ids = await build_world(session)
    marker = f"rollback-{uuid.uuid4().hex[:8]}"

    before = (
        await session.execute(text("SELECT count(*) FROM audit_log"))
    ).scalar_one()

    savepoint = await session.begin_nested()
    await audit.append(
        session,
        action="case.note_added",
        entity_type="pending_case",
        entity_id=ids["case"],
        after={"marker": marker},
    )
    await savepoint.rollback()

    after = (await session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
    assert after == before

    found = (
        await session.execute(
            text("SELECT count(*) FROM audit_log WHERE after::text LIKE :m"),
            {"m": f"%{marker}%"},
        )
    ).scalar_one()
    assert found == 0
