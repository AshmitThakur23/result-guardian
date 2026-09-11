"""Declarative base. Phase 0.5.

Phase 1 models import ``Base`` from here and are registered on it so Alembic's
autogenerate sees them.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Phase 1 onwards: import model modules here so Alembic autogenerate picks
# them up, e.g.
#     from app.db.models import patients, encounters, orders  # noqa: F401
