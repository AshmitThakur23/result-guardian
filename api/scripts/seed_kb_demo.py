"""Put one approved document in the knowledge base, so Phase 8 has something
to retrieve. DEMO ONLY.

    docker compose exec -T api python scripts/seed_kb_demo.py

## Why this script exists

The knowledge base is emptied by any full test run — ``test_migration_round_trip``
downgrades and re-upgrades, which drops and recreates ``kb_documents`` and
``kb_chunks``. That is correct behaviour for a test, and it means a knowledge
base populated by hand through the API disappears the next time anyone runs the
suite. Seeding it from a script makes it reproducible in one command instead of
a thing somebody has to remember.

## ⛔ What this text is, and is not

The passage below is a **fabricated excerpt written for a demonstration**. It is
not a real hospital policy, it has not been reviewed by a clinician, and it must
never be presented as clinical guidance or counted toward any exit gate.

It says so in its own title, in its ``source_ref``, and in the publisher field —
in three places, because the whole point of Phase 8 is that a clinician can see
where a claim came from, and a demo fixture masquerading as hospital policy
would defeat that at the first step.

**What it is for:** proving the *mechanism* — that retrieval finds approved
guidance, that generation quotes it, and that the span verifier catches a
quotation the source does not contain. None of that needs the content to be
authoritative; it needs the content to be *checkable*, which a known fixture is.

**When real guidance arrives** — the hospital's own antibiotic policy or
antibiogram — load it through ``POST /api/kb/documents``, approve it, and delete
this. Two documents, one real and one fabricated, is exactly the situation
``approved_by`` exists to prevent.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.rag.ingest import ingest_document

TITLE = "DEMO FIXTURE — not real hospital policy — Antibiotic Policy excerpt"

#: Deliberately plain, deliberately short, and deliberately about one thing.
#: A long document would make it harder to see, at a glance, whether a quoted
#: sentence really is in the source — and being able to check that by eye is the
#: point of the demonstration.
POLICY = """4.2 Urinary tract infection

Escherichia coli isolated from urine at greater than 100,000 CFU per mL with
resistance to ceftriaxone requires review of the discharge prescription before
the patient continues therapy. Ceftriaxone is a third-generation cephalosporin
and an organism reported resistant to it will not respond. Alternative agents
should be selected from the reported sensitivity panel rather than empirically.

4.3 Escalation after discharge

Where a resistant organism is identified after the patient has left the ward,
the responsible clinician must be contacted within twenty-four hours. The
prescription should be changed before the next dose is due.

4.4 Documentation

Every change of antimicrobial therapy must be recorded with the organism, the
sensitivity result and the reason for the change.
"""


async def main() -> int:
    async with get_sessionmaker()() as session:
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM kb_documents "
                    " WHERE title = :t AND deleted_at IS NULL"
                ),
                {"t": TITLE},
            )
        ).first()
        if existing is not None:
            print(f"Already present: {existing.id}")
            return 0

        # An approver is required: `ck_kb_documents_approval_complete` refuses a
        # row with an approval time and no approver, and rightly so.
        approver = (
            await session.execute(
                text(
                    "SELECT id, employee_code FROM users "
                    " WHERE role = 'admin' AND deleted_at IS NULL "
                    " ORDER BY created_at LIMIT 1"
                )
            )
        ).first()
        if approver is None:
            print("No admin user to approve as. Run scripts/seed_dev.py first.")
            return 1

        doc_id, chunks, warnings = await ingest_document(
            session,
            title=TITLE,
            publisher="hospital",
            doc_type="antibiotic_policy",
            document_text=POLICY,
            version="demo-1",
            source_ref=(
                "FABRICATED for the Result Guardian demonstration. Not a real "
                "policy and not clinically reviewed. Replace with the hospital's "
                "own document before any clinical use."
            ),
            # Approved on ingest, because a demo that requires two commands to
            # become usable will be run wrong under pressure.
            approved_by=approver.id,
        )
        await session.commit()

    print(f"Seeded {chunks} chunk(s) as {doc_id}")
    print(f"  approved by {approver.employee_code}")
    if warnings:
        print(f"  ⚠️  {len(warnings)} possible identifier(s) flagged — unexpected here")
    print()
    print("⛔ This is a DEMO FIXTURE, not hospital policy. Replace it with a")
    print("   real approved document before any clinical use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
