"""Give the seeded dev users a password so you can actually log in. DEV ONLY.

Why this exists: the Phase 1.5 seeder predates Phase 5 authentication, so the
users it creates have **no `password_hash` at all** and cannot log in. Every
session that wants to look at the UI hits this, and the answer has so far lived
only in chat -- which CLAUDE.md warns is a decision that gets re-litigated next
session. So it lives here instead.

    docker compose exec -T api python scripts/reset_dev_passwords.py
    docker compose exec -T api python scripts/reset_dev_passwords.py --password 'something-else'

⛔ **Never run this against anything but a local development database.** It
refuses to run unless ``RG_ENV`` is unset or ``dev``/``local``/``test``, because
a script that sets a known password on every named account is precisely what you
do not want pointed at a hospital.

It touches only the five named role accounts below. It never creates a user,
never changes a role, and never touches the E2E fixtures.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.auth import hash_password

# One account per role, so every screen can be exercised.
DEV_ACCOUNTS = ("ADMIN1", "DOC1", "LAB1", "HEAD1", "AUDIT1")

DEFAULT_PASSWORD = "ResultGuardian#2026"

SAFE_ENVS = {"", "dev", "development", "local", "test"}


def _guard() -> None:
    env = os.getenv("RG_ENV", "").strip().lower()
    if env not in SAFE_ENVS:
        sys.exit(
            f"refusing to run: RG_ENV={env!r}. This script sets a known password "
            "on named accounts and is for local development only."
        )


async def _reset(password: str) -> int:
    digest = hash_password(password)
    async with get_sessionmaker()() as session:
        result = await session.execute(
            text(
                "UPDATE users SET password_hash = :h, must_change_password = false "
                "WHERE employee_code = ANY(:codes) AND deleted_at IS NULL "
                "RETURNING employee_code, role"
            ),
            {"h": digest, "codes": list(DEV_ACCOUNTS)},
        )
        rows = result.fetchall()
        await session.commit()

    if not rows:
        print(
            "No matching users. Seed first:\n"
            "  docker compose exec -T api python scripts/seed_dev.py"
        )
        return 1

    print(f"Password set on {len(rows)} account(s):\n")
    for code, role in sorted(rows, key=lambda r: r[1]):
        print(f"  {code:<8} {role}")
    print(f"\n  password: {password}")
    print("\nOpen http://localhost/ and sign in with any of the codes above.")

    missing = sorted(set(DEV_ACCOUNTS) - {r[0] for r in rows})
    if missing:
        print(f"\n⚠️  not found in this database: {', '.join(missing)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--password",
        default=os.getenv("RG_DEV_PASSWORD", DEFAULT_PASSWORD),
        help="password to set (default: RG_DEV_PASSWORD env var, else a built-in dev value)",
    )
    args = parser.parse_args()
    _guard()
    return asyncio.run(_reset(args.password))


if __name__ == "__main__":
    raise SystemExit(main())
