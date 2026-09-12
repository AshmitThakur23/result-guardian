"""Declarative base. Phase 0.5.

Phase 1 models import ``Base`` from here and are registered on it so Alembic's
autogenerate sees them.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Importing the models package is what registers every model on
# Base.metadata, which is what Alembic autogenerate compares the database
# against. The import is "unused" by design -- the import *is* the
# registration -- hence the pragma.
#
# It sits below the class because the model modules import Base from here.
from app.db import models  # noqa: E402,F401
