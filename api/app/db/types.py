"""Base conventions as code, not prose. Phase 0.6.

The build plan flags these as "decide now, costly later". Encoding them as
mixins means every future table inherits them by construction rather than by
remembering to.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

try:  # pragma: no cover - import shim
    from uuid_extensions import uuid7 as _uuid7
except ImportError:  # pragma: no cover
    _uuid7 = None


def uuid7() -> uuid.UUID:
    """Time-sortable UUIDv7 primary key.

    Time-sortable matters beyond tidiness: Phase 5.7 specifies cursor-based
    pagination keyed on the PK, which requires monotonic ids.
    """
    if _uuid7 is not None:
        return _uuid7()  # type: ignore[no-any-return]
    return uuid.uuid4()  # fallback; never reached once uuid7 is installed


class UUIDPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid7
    )


class TimestampMixin:
    """TIMESTAMPTZ everywhere, stored UTC, displayed IST at the edge."""

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ActorMixin:
    """Who did it. Populated from the request's authenticated user."""

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SoftDeleteMixin:
    """Clinical rows are never hard-deleted.

    A deleted result is evidence in a medico-legal question (Phase 10.3).
    """

    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


def text_enum(column_name: str, allowed: tuple[str, ...]) -> str:
    """Build a CHECK expression for a text-backed enum.

    Enums are text + CHECK rather than PG enum types: altering a PG enum in a
    migration is painful, and these value sets will change per hospital.
    """
    values = ", ".join(f"'{v}'" for v in allowed)
    return f"{column_name} IN ({values})"


__all__ = [
    "ActorMixin",
    "SoftDeleteMixin",
    "String",
    "TimestampMixin",
    "UUIDPkMixin",
    "text",
    "text_enum",
    "uuid7",
]
