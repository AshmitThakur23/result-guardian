"""The hash-chained audit log. Phase 5.5 ★

    row_hash = SHA256(seq || occurred_at || actor || action || entity ||
                      canonical_json(before) || canonical_json(after) ||
                      prev_hash)

    Canonical JSON: **sorted keys, no whitespace, UTC ISO-8601** — hashing must
    be reproducible.

That last clause is the whole requirement. A hash that cannot be recomputed
six months later on a different machine is not evidence of anything, so every
input is normalised before it is hashed:

* **keys sorted**, so ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` hash the same;
* **no whitespace**, so a pretty-printer cannot change history;
* **timestamps as UTC ISO-8601 with an explicit offset**, so the same instant
  recorded in IST and UTC produces one hash;
* **``Decimal`` as its string form**, never a float — ``0.1`` is not
  representable in binary and a float round-trip would change the hash;
* **UUIDs as their canonical lowercase string**.

``ensure_ascii=False`` is deliberate: a Hindi note must hash the same whether
the escaping happens or not, so it never happens.

**Writes happen in the same transaction as the change — never
fire-and-forget.** ``append()`` takes the caller's session and does not
commit. If the business change rolls back, so does its audit row; there is no
window in which the log claims something that did not happen, and none in
which something happened unlogged.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The first row's prev_hash. Sixty-four zeros: a genesis value that is
# obviously not a real hash, so a chain starting anywhere else is visible.
GENESIS_HASH = "0" * 64

# Actions. Free-form strings would drift into six spellings of the same event,
# which is exactly what makes an audit log unqueryable.
ACTION_CASE_ACKNOWLEDGED = "case.acknowledged"
ACTION_CASE_CLOSED = "case.closed"
ACTION_CASE_REOPENED = "case.reopened"
ACTION_CASE_REASSIGNED = "case.reassigned"
ACTION_CASE_NOTE_ADDED = "case.note_added"
ACTION_LOGIN_SUCCEEDED = "auth.login_succeeded"
ACTION_LOGIN_FAILED = "auth.login_failed"
ACTION_LOGOUT = "auth.logout"
ACTION_TOKEN_REFRESHED = "auth.token_refreshed"
ACTION_ACCOUNT_LOCKED = "auth.account_locked"
ACTION_PASSWORD_CHANGED = "auth.password_changed"
ACTION_BREAK_GLASS = "auth.break_glass"
ACTION_CONFIG_CHANGED = "config.changed"
ACTION_ROSTER_CHANGED = "roster.changed"


def _canonical(value: Any) -> Any:
    """Normalise a value into something ``json.dumps`` hashes reproducibly."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        # A float in an audit hash is a reproducibility bug waiting for a
        # different platform. Carry it as its shortest round-trip string.
        return repr(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        # Naive timestamps are ambiguous; assume UTC rather than the server's
        # locale, and say so in the output by always carrying the offset.
        moment = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
        return moment.astimezone(dt.UTC).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return str(value)


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, UTC ISO-8601. ``None`` becomes ``"null"``."""
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def compute_row_hash(
    *,
    seq: int,
    occurred_at: dt.datetime,
    actor_user_id: uuid.UUID | str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    prev_hash: str,
) -> str:
    """The plan's formula, with every field canonicalised first.

    ``|`` separates the fields so two different splits cannot produce the same
    byte string — without a separator, ``action="ab"`` + ``entity="c"`` and
    ``action="a"`` + ``entity="bc"`` would hash identically.
    """
    parts = [
        str(seq),
        _canonical(occurred_at),
        str(actor_user_id) if actor_user_id else "",
        action,
        entity_type,
        entity_id,
        canonical_json(before),
        canonical_json(after),
        prev_hash,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRow:
    seq: int
    row_hash: str
    prev_hash: str
    occurred_at: dt.datetime


async def append(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    actor_ip: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    break_glass_reason: str | None = None,
    occurred_at: dt.datetime | None = None,
) -> AuditRow:
    """Append one row to the chain, **in the caller's transaction**.

    ``rg_audit_next_seq()`` takes a transaction-scoped advisory lock, so two
    concurrent appends serialise here rather than both reading the same head
    and forking the chain. The lock is released by commit or rollback, with no
    cleanup path to forget.

    **The two reads are deliberately two statements.** Folding them into one
    ``SELECT rg_audit_next_seq(), (SELECT row_hash …)`` looks tidier and is
    wrong: under READ COMMITTED the statement's snapshot is taken when the
    statement *starts*, which is before the function inside it acquires the
    lock. The waiting transaction would then allocate a correct ``seq`` but
    read a ``prev_hash`` from before its predecessor committed — and the chain
    forks with no duplicate seq to give it away. Issuing the lock first and
    reading the head second gives the second statement a fresh, post-lock
    snapshot. This was caught by
    ``test_concurrent_appends_do_not_fork_the_chain``, not by reading.
    """
    moment = occurred_at or dt.datetime.now(dt.UTC)

    seq = int(
        (await session.execute(text("SELECT rg_audit_next_seq() AS seq"))).scalar_one()
    )
    prev_hash = (
        await session.execute(
            text("SELECT row_hash FROM audit_log ORDER BY seq DESC LIMIT 1")
        )
    ).scalar() or GENESIS_HASH

    row_hash = compute_row_hash(
        seq=seq,
        occurred_at=moment,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=before,
        after=after,
        prev_hash=prev_hash,
    )

    await session.execute(
        text(
            "INSERT INTO audit_log "
            "(seq, occurred_at, actor_user_id, actor_ip, action, entity_type, "
            " entity_id, before, after, prev_hash, row_hash, break_glass_reason) "
            "VALUES (:seq, :at, :actor, CAST(:ip AS inet), :action, :etype, :eid, "
            "        CAST(:before AS jsonb), CAST(:after AS jsonb), :prev, :hash, "
            "        :bg)"
        ),
        {
            "seq": seq,
            "at": moment,
            "actor": str(actor_user_id) if actor_user_id else None,
            "ip": actor_ip,
            "action": action,
            "etype": entity_type,
            "eid": str(entity_id),
            "before": canonical_json(before) if before is not None else None,
            "after": canonical_json(after) if after is not None else None,
            "prev": prev_hash,
            "hash": row_hash,
            "bg": break_glass_reason,
        },
    )
    return AuditRow(seq=seq, row_hash=row_hash, prev_hash=prev_hash, occurred_at=moment)


@dataclass
class ChainVerification:
    """What ``GET /api/audit/verify`` answers."""

    intact: bool
    rows_checked: int
    first_break_seq: int | None = None
    first_break_reason: str | None = None
    head_seq: int | None = None
    head_hash: str | None = None


async def verify_chain(
    session: AsyncSession,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> ChainVerification:
    """*"Recomputes the chain, returns the first break if any."*

    Three ways a chain can be broken, and each is reported distinctly because
    they mean different things:

    * **``row_hash`` mismatch** — a row's own contents were changed.
    * **``prev_hash`` mismatch** — a row was removed or inserted between two
      others.
    * **``seq`` gap** — a row was deleted outright.

    Verifying a *window* rather than the whole chain is what the endpoint's
    ``from``/``to`` are for, and it is honest about one thing: a window that
    does not start at seq 1 cannot check its own first ``prev_hash`` against
    anything, so it takes that row's recorded ``prev_hash`` as its starting
    point and says so by counting from there.
    """
    sql = (
        "SELECT seq, occurred_at, actor_user_id, action, entity_type, entity_id, "
        "       before, after, prev_hash, row_hash "
        "  FROM audit_log WHERE true"
    )
    params: dict[str, Any] = {}
    if since is not None:
        sql += " AND occurred_at >= :since"
        params["since"] = since
    if until is not None:
        sql += " AND occurred_at <= :until"
        params["until"] = until
    sql += " ORDER BY seq"

    rows = (await session.execute(text(sql), params)).all()
    if not rows:
        return ChainVerification(intact=True, rows_checked=0)

    expected_prev: str | None = None
    expected_seq: int | None = None

    for index, row in enumerate(rows):
        if index == 0:
            expected_prev = row.prev_hash
            expected_seq = int(row.seq)

        if expected_seq is not None and int(row.seq) != expected_seq:
            return ChainVerification(
                intact=False,
                rows_checked=index,
                first_break_seq=int(row.seq),
                first_break_reason=(
                    f"seq gap: expected {expected_seq}, found {row.seq} — a row "
                    "was deleted or the chain was re-numbered"
                ),
            )

        if row.prev_hash != expected_prev:
            return ChainVerification(
                intact=False,
                rows_checked=index,
                first_break_seq=int(row.seq),
                first_break_reason=(
                    "prev_hash does not match the previous row's row_hash — a "
                    "row was removed or inserted"
                ),
            )

        recomputed = compute_row_hash(
            seq=int(row.seq),
            occurred_at=row.occurred_at,
            actor_user_id=row.actor_user_id,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            before=row.before,
            after=row.after,
            prev_hash=row.prev_hash,
        )
        if recomputed != row.row_hash:
            return ChainVerification(
                intact=False,
                rows_checked=index,
                first_break_seq=int(row.seq),
                first_break_reason=(
                    "row_hash does not match the row's contents — this row was "
                    "modified after it was written"
                ),
            )

        expected_prev = row.row_hash
        expected_seq = int(row.seq) + 1

    last = rows[-1]
    return ChainVerification(
        intact=True,
        rows_checked=len(rows),
        head_seq=int(last.seq),
        head_hash=last.row_hash,
    )


async def anchor_chain(session: AsyncSession) -> ChainVerification:
    """The nightly notarisation. Phase 5.5.

    Verifies the whole chain and publishes the head hash into
    ``audit_anchors``. The anchor is only worth something once it leaves the
    machine — the runbook says to export it — because an attacker with the
    database has both the log and the anchor otherwise.
    """
    result = await verify_chain(session)
    await session.execute(
        text(
            "INSERT INTO audit_anchors "
            "(head_seq, head_hash, rows_verified, chain_intact, first_break_seq) "
            "VALUES (:s, :h, :n, :ok, :brk)"
        ),
        {
            "s": result.head_seq or 0,
            "h": result.head_hash or GENESIS_HASH,
            "n": result.rows_checked,
            "ok": result.intact,
            "brk": result.first_break_seq,
        },
    )
    return result
