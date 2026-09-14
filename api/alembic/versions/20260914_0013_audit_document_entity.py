"""Let the audit log name a document. Phase 6.

``audit_log.entity_type`` carries a CHECK listing every kind of thing the log
may refer to, and Phase 6 added a kind: ``document``. Without this migration,
**every upload fails** — ``intake.accept_upload`` writes its audit row in the
same transaction as the document row, so the CHECK violation rolls back the
upload itself.

That is the constraint working exactly as designed. A free-text
``entity_type`` would have accepted ``document``, ``documents`` and ``Document``
as three different things and left the log unqueryable; this way a new entity
kind is a deliberate, reviewed migration. The cost is remembering to write it,
and the way that gets remembered is a failing test.

Found by ``test_phase_6_pipeline.py`` on its first end-to-end run against a
real database — not by reading, and not by any unit test, because nothing
below the pipeline touches the audit log.

Revision ID: 0013_audit_document_entity
Revises: 0012_document_ingestion
"""

from __future__ import annotations

from alembic import op

revision: str = "0013_audit_document_entity"
down_revision: str | None = "0012_document_ingestion"
branch_labels: str | None = None
depends_on: str | None = None

# Kept in the same order as `app.db.models.audit.AUDIT_ENTITY_TYPES`, which is
# the list this constraint must agree with.
ENTITY_TYPES = (
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
    "document",
)

PREVIOUS = ENTITY_TYPES[:-1]


def _rendered(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # `audit_log` is append-only, enforced by trigger -- but a CHECK is table
    # metadata, not a row, so altering it does not touch the trigger and does
    # not rewrite history. Existing rows are re-validated against the new
    # constraint, which is safe in this direction: widening the list can never
    # invalidate a row that was already legal.
    op.drop_constraint("ck_audit_log_entity_type", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_entity_type",
        "audit_log",
        f"entity_type IN ({_rendered(ENTITY_TYPES)})",
    )


def downgrade() -> None:
    """Narrow the list again — **without making the downgrade impossible.**

    ``NOT VALID`` is the whole point of this function, and it is not a
    shortcut. Postgres validates a new CHECK against existing rows by
    default, so the obvious implementation refuses to run the moment a single
    ``document`` row exists in the log. And ``audit_log`` is append-only: those
    rows can never be deleted, by design. The obvious implementation therefore
    makes the migration chain **permanently** undowngradable after the first
    upload — which would strand a hospital on a version it wanted to roll back.

    ``NOT VALID`` says "do not re-check what is already there, do enforce this
    on everything written from now on". History survives; a downgraded
    deployment still cannot record a *new* document event, which is the
    behaviour that was being asked for.

    Caught by ``test_migration_round_trip.py``, which downgrades the whole
    chain and brings it back up — a test written in Phase 1 that found a
    Phase 6 defect five phases later.
    """
    op.drop_constraint("ck_audit_log_entity_type", "audit_log", type_="check")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_log_entity_type "
        f"CHECK (entity_type IN ({_rendered(PREVIOUS)})) NOT VALID"
    )
