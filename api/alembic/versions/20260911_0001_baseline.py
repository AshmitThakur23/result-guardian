"""baseline — extensions and queues are created by infra/postgres/init

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-11

Deliberately empty. Extensions (01-extensions.sql) and pgmq queues
(02-queues.sql) run from the Postgres init directory on first container
start, before Alembic ever connects. Phase 1 revision 002 adds the real
schema on top of this.
"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
