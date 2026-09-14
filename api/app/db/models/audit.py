"""Sessions and the hash-chained audit log. Phase 5.1 / 5.5 ★

**The audit log is the one table in this system that is designed to be
un-rewritable.** Everything else can be corrected; this is the record that
somebody did the correcting. The plan gives the shape:

    ``audit_log`` — id BIGSERIAL, seq, occurred_at, actor_user_id, actor_ip,
    action, entity_type, entity_id, before JSONB, after JSONB,
    prev_hash CHAR(64), row_hash CHAR(64)

    row_hash = SHA256(seq || occurred_at || actor || action || entity ||
                      canonical_json(before) || canonical_json(after) ||
                      prev_hash)

Each row's hash covers the previous row's hash, so the rows form a chain.
Changing any historical row changes its hash, which breaks every hash after
it — and the break is *findable*, which is the whole point. A tamperer would
have to rewrite every subsequent row, and the nightly notarisation
(migration 0010) publishes the head hash outside the table so even that is
detectable.

Three defences, deliberately layered, because each one alone is defeatable:

1. **A trigger** raises on UPDATE and DELETE. Defeated by a superuser.
2. **REVOKE UPDATE, DELETE** from the application role. Defeated by a
   different role.
3. **The hash chain.** Not defeated by database access at all — only by
   rewriting every later row *and* the published head hashes.

``seq`` is a separate column from ``id`` on purpose. BIGSERIAL gaps on
rollback; the chain must not. ``seq`` is assigned under an advisory lock so
it is gapless and ordered, which is what makes "recompute the chain" a
well-defined operation.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
)

# The advisory lock that serialises `seq`. An arbitrary but fixed key: two
# writers must pick the same number or the lock does nothing.
AUDIT_SEQ_LOCK_KEY = 0x52475F41554449  # "RG_AUDI"

# What an audit row can be about. Kept small and additive -- an unknown
# entity_type is refused rather than stored, because an audit trail nobody can
# query by entity is a log file with extra steps.
AUDIT_ENTITY_TYPES = (
    "pending_case",
    "discharge_contract",
    "encounter",
    "user",
    "duty_roster",
    "user_absence",
    "escalation_chain",
    "panic_threshold",
    "clinical_keyword",
    "session",
    "notification",
    "result",
    # Phase 6. A document is evidence: how it entered the building, what was
    # read out of it, and every retry all belong in the chain.
    "document",
)


class Session(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One refresh token, stored hashed. Phase 5.1.

        JWT access token (**15 min**) + refresh token (**12h**, rotated,
        stored hashed in ``sessions``)

    **Hashed, never stored raw.** A stolen database dump must not be a stack
    of working refresh tokens. The access token is not stored at all — it is
    short-lived and stateless by design, which is what makes 15 minutes the
    right number.

    ``rotated_to_id`` records the successor when a refresh is used, so a
    replayed refresh token is detectable: presenting a token that has already
    been rotated is either a bug or a theft, and both deserve the whole family
    being revoked.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # SHA-256 of the refresh token. Not Argon2: this is a high-entropy random
    # token, not a password, so there is nothing to brute-force and a fast
    # hash keeps refresh cheap.
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rotated_to_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)

    __table_args__ = (
        UniqueConstraint("refresh_token_hash", name="uq_sessions_refresh_hash"),
        CheckConstraint("expires_at > issued_at", name="ck_sessions_window_ordered"),
        Index(
            "ix_sessions_user_live",
            "user_id",
            postgresql_where=text("revoked_at IS NULL AND deleted_at IS NULL"),
        ),
    )


class AuditLog(Base):
    """The hash-chained audit log. Phase 5.5 ★

    Deliberately **not** using the project's usual mixins. No ``deleted_at``
    (soft-deleting an audit row is a contradiction), no ``updated_at``
    (nothing is ever updated), no UUID primary key (the chain needs a
    monotonic integer). This table breaks the house conventions because the
    conventions assume rows can change, and this table's entire purpose is
    that they cannot.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Gapless and ordered, assigned under an advisory lock. `id` may gap on a
    # rolled-back transaction; the chain cannot.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # The chain. CHAR(64) because SHA-256 hex is exactly 64 characters, and a
    # wrong-length hash should be refused by the column rather than discovered
    # by the verifier.
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Break-glass access is logged loudly (5.1), and this is what makes it
    # loud: a flag the audit viewer can filter on.
    break_glass_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("seq", name="uq_audit_log_seq"),
        UniqueConstraint("row_hash", name="uq_audit_log_row_hash"),
        CheckConstraint(
            text_enum("entity_type", AUDIT_ENTITY_TYPES),
            name="ck_audit_log_entity_type",
        ),
        CheckConstraint("seq > 0", name="ck_audit_log_seq_positive"),
        CheckConstraint("length(row_hash) = 64", name="ck_audit_log_row_hash_length"),
        CheckConstraint("length(prev_hash) = 64", name="ck_audit_log_prev_hash_length"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_actor", "actor_user_id"),
        Index(
            "ix_audit_log_break_glass",
            "occurred_at",
            postgresql_where=text("break_glass_reason IS NOT NULL"),
        ),
    )


class AuditAnchor(Base):
    """The nightly notarisation. Phase 5.5.

        Nightly ``pg_cron`` job verifies the chain and publishes the head hash
        to a separate append-only file (a cheap notarisation).

    A separate table rather than a file, for one reason worth stating: a file
    on the same disk is no harder to rewrite than the table. What makes this
    useful is that the anchors are **exported off the machine** (the runbook
    says so) — at which point rewriting history requires also rewriting every
    copy of the anchor that has left the building.
    """

    __tablename__ = "audit_anchors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    anchored_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    head_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    head_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rows_verified: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chain_intact: Mapped[bool] = mapped_column(nullable=False)
    first_break_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(chain_intact) = (first_break_seq IS NULL)",
            name="ck_audit_anchors_break_matches_intact",
        ),
        Index("ix_audit_anchors_anchored_at", "anchored_at"),
    )
