#!/usr/bin/env python3
"""Regenerate the README's stats badges by COUNTING the repository.

Why this exists
---------------
A README that states "1411 tests" is making a claim. Claims rot: somebody
deletes a test file, somebody adds twelve endpoints, and the number in the
README keeps asserting whatever it asserted the day it was typed. This repo's
standing rule is that a number nobody measured is a number nobody should trust,
so the four badges between the STATS markers are measured on every push to main.

It counts, it does not estimate. Every number below maps to a grep you could run
yourself, and the counting rules are written next to each one so a surprising
figure can be argued with rather than believed.

Exit codes
----------
0  README already matched the repository, or was updated successfully
1  the STATS markers are missing -- someone removed them from the README
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

START = "<!-- STATS:START -->"
END = "<!-- STATS:END -->"

# The hero table repeats the test count. One source of truth or none: if the
# badge block is regenerated and the hero is not, the README contradicts itself
# on its own front page.
H_START = "<!-- STATS:HEADER:START -->"
H_END = "<!-- STATS:HEADER:END -->"


def count_tables() -> int:
    """SQLAlchemy models, counted by __tablename__ assignments.

    Association tables declared via sa.Table() are deliberately NOT counted --
    they are join tables, not entities, and including them would inflate the
    figure in a way a reader inspecting the models directory could not reproduce.
    """
    n = 0
    for f in (ROOT / "api" / "app" / "db" / "models").rglob("*.py"):
        n += len(re.findall(r"^\s*__tablename__\s*=", f.read_text("utf-8"), re.M))
    return n


def count_migrations() -> int:
    """Alembic revisions. One file is one revision; __init__.py is not one."""
    d = ROOT / "api" / "alembic" / "versions"
    return len([f for f in d.glob("*.py") if f.name != "__init__.py"])


def count_endpoints() -> int:
    """HTTP routes, counted by decorator.

    Counts @router.<verb> and @app.<verb>. A path registered twice under two
    verbs is two endpoints, which matches how the OpenAPI schema counts them.
    """
    pat = re.compile(r"^\s*@(?:router|app)\.(get|post|put|patch|delete)\(", re.M)
    n = 0
    for f in (ROOT / "api" / "app").rglob("*.py"):
        n += len(pat.findall(f.read_text("utf-8")))
    return n


def count_tests() -> int:
    """Backend + frontend + E2E tests.

    Backend: `def test_*` / `async def test_*` in api/tests.
    Frontend and E2E: `it(` / `test(` in web, excluding node_modules.

    Parametrised cases count ONCE here, as one written test, even though pytest
    reports one result per parameter. That is the conservative direction: this
    badge will read lower than a pytest summary, never higher.
    """
    n = 0
    for f in (ROOT / "api" / "tests").rglob("test_*.py"):
        n += len(re.findall(r"^\s*(?:async\s+)?def\s+test_", f.read_text("utf-8"), re.M))

    web = ROOT / "web"
    js = re.compile(r"^\s*(?:it|test)\s*(?:\.\w+)?\s*\(", re.M)
    for pattern in ("src/**/*.test.ts", "src/**/*.test.tsx", "e2e/**/*.spec.ts"):
        for f in web.glob(pattern):
            if "node_modules" in f.parts:
                continue
            n += len(js.findall(f.read_text("utf-8")))
    return n


def badge(label: str, value: int, colour: str) -> str:
    return (
        f'<img src="https://img.shields.io/badge/{label}-{value}-{colour}'
        f'?style=for-the-badge"/>'
    )


def main() -> int:
    text = README.read_text("utf-8")
    if START not in text or END not in text:
        print(f"error: {START} / {END} markers not found in README.md", file=sys.stderr)
        return 1

    stats = [
        ("TABLES", count_tables(), "0ea5e9"),
        ("MIGRATIONS", count_migrations(), "a855f7"),
        ("ENDPOINTS", count_endpoints(), "22c55e"),
        ("TESTS", count_tests(), "f97316"),
    ]
    for label, value, _ in stats:
        print(f"  {label:<12} {value}")

    block = f"{START}\n" + " ".join(badge(*s) for s in stats) + f"\n{END}"
    updated = re.sub(
        re.escape(START) + r".*?" + re.escape(END), block, text, flags=re.S
    )

    tests = dict((label, value) for label, value, _ in stats)["TESTS"]
    hero = (
        f'{H_START}<img src="https://img.shields.io/badge/{tests}-94a3b8'
        f'?style=for-the-badge"/>{H_END}'
    )
    updated = re.sub(
        re.escape(H_START) + r".*?" + re.escape(H_END), hero, updated, flags=re.S
    )

    if updated == text:
        print("README already up to date.")
        return 0

    README.write_text(updated, "utf-8")
    print("README stats updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
