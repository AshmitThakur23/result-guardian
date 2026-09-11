"""baseline — worker_health; extensions and queues come from infra/postgres/init

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-11

Extensions (01-extensions.sql) and pgmq queues (02-queues.sql) run from the
Postgres init directory on first container start, before Alembic ever
connects, so they are deliberately absent here.

``worker_health`` is the exception, and is owned by this migration rather than
by an init script. It is an application table, and init scripts run only
against an empty data directory -- so a database built by migrations alone
(CI, testcontainers, a restored volume) would otherwise not have it, and the
worker's heartbeat would fail on every beat.

Phase 1 revision 002 adds the clinical schema on top of this.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Deliberately not using the Phase 0.6 mixins: this is infrastructure
    # telemetry, not a clinical row. It has no UUID PK, no soft delete and no
    # actor columns, because it is keyed by worker identity and is safe to
    # hard-delete. Every *clinical* table from Phase 1 onward does use them.
    op.create_table(
        "worker_health",
        sa.Column("worker_name", sa.Text(), primary_key=True),
        sa.Column(
            "last_beat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("version", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("worker_health")
