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
#     from app.db.models import patients, encounters, orders
#
# Those imports are intentionally "unused" -- importing is what registers the
# models on Base -- so each will need an F401 suppression pragma.
