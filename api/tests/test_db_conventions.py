"""Phase 0.6 — the base conventions, asserted rather than assumed.

The build plan calls these "decide now, costly later". They are encoded as
mixins in app/db/types.py so every Phase 1 table inherits them by
construction; these tests are what stop that encoding from quietly rotting
before Phase 1 arrives.
"""

from __future__ import annotations

import time
import uuid

from app.db import base, types
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
    uuid7,
)


def test_uuid7_is_a_real_uuid7() -> None:
    value = uuid7()
    assert isinstance(value, uuid.UUID)
    # The fallback to uuid4 exists only if the library is missing; in CI and in
    # the image it is installed, so this must genuinely be a v7.
    if types._uuid7 is not None:
        assert value.version == 7


def test_uuid7_is_time_sortable() -> None:
    """Not cosmetic: Phase 5.7 pages on the PK, which needs monotonic ids."""
    generated = []
    for _ in range(5):
        generated.append(uuid7())
        time.sleep(0.002)
    assert generated == sorted(generated)


def test_uuid7_values_are_unique() -> None:
    assert len({uuid7() for _ in range(2000)}) == 2000


def test_text_enum_builds_a_check_constraint() -> None:
    assert text_enum("status", ("active", "discharged")) == (
        "status IN ('active', 'discharged')"
    )


def test_text_enum_handles_a_single_value() -> None:
    assert text_enum("severity", ("critical",)) == "severity IN ('critical')"


def test_mixins_declare_the_required_columns() -> None:
    """Every table gets created_at, updated_at, created_by, updated_by."""
    assert "id" in UUIDPkMixin.__annotations__
    assert {"created_at", "updated_at"} <= set(TimestampMixin.__annotations__)
    assert {"created_by", "updated_by"} <= set(ActorMixin.__annotations__)
    # Soft delete, because clinical rows are never hard-deleted.
    assert "deleted_at" in SoftDeleteMixin.__annotations__


def test_declarative_base_is_importable_for_alembic_autogenerate() -> None:
    assert hasattr(base.Base, "metadata")
