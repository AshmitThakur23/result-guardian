"""Score extraction and matching against a labelled gold set. Phase 7.7.

    docker compose exec -T api python scripts/eval_extraction.py

Pass ``--gold`` and ``--actual`` to score a set other than the default.

7.7: *"**Run on every PR touching extraction** — your only defence against
silent regression."*

## 🔴 There is nothing to measure yet, and the exit code says so

Exit Gate 7 is defined over **100 labelled real documents** and the corpus is
**0** — the same missing corpus that holds Exit Gate 6 open. With no gold set
this script exits **0 with a clear message**, so CI stays green and nobody is
tempted to invent data to turn it green.

It exits **1** only when a gold set exists *and* the measured numbers fail the
gate. Those are different states and they must not share an exit code:

* no data → **undecidable** → exit 0, and say so loudly
* data, gate met → exit 0
* data, gate missed → exit 1

## ⛔ Never point this at synthetic documents

A generated PDF contains exactly the layouts its generator was written for, so
it scores near-perfectly and measures the generator rather than the extractor.
The build plan is explicit that synthetic reports are not gate evidence, and a
scoring script is the easiest place in the codebase to forget it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Run as `python scripts/eval_extraction.py`, `sys.path[0]` is `scripts/` and
# not the project root, so `import app...` finds the *installed* copy in
# site-packages instead of the working tree. That copy is whatever was baked
# into the image, so a module added since the last build is simply missing --
# which reads as "no such module" rather than "your code is not being used".
#
# Phase 6 hit exactly this and diagnosed it as a PYTHONPATH problem. Fixing it
# here instead means the script is correct however it is invoked.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.services.extraction.evaluate import format_report, score_documents

#: Where the gold set lives when there is one. Outside git, like the corpus —
#: it contains real patient reports, de-identified or not.
DEFAULT_GOLD = pathlib.Path("/data/gold/extraction_gold.json")
DEFAULT_ACTUAL = pathlib.Path("/data/gold/extraction_actual.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=pathlib.Path, default=DEFAULT_GOLD)
    parser.add_argument("--actual", type=pathlib.Path, default=DEFAULT_ACTUAL)
    args = parser.parse_args()

    if not args.gold.exists():
        print("Extraction evaluation: NOTHING TO MEASURE")
        print("=" * 60)
        print(f"  No gold set at {args.gold}")
        print()
        print("  Exit Gate 7 is defined over 100 labelled REAL documents and")
        print("  the corpus is at 0. This is the honest state, not a failure,")
        print("  so this exits 0 and CI stays green.")
        print()
        print("  ⛔ Do NOT populate this with synthetic documents to make a")
        print("     number appear. A generated PDF measures the generator.")
        print()
        print("  ▶ docs/phase-6-outstanding.md §4 — the corpus request")
        return 0

    expected = json.loads(args.gold.read_text(encoding="utf-8"))
    if not args.actual.exists():
        print(f"Gold set found at {args.gold}, but no extraction output at")
        print(f"{args.actual}. Run the extraction over the corpus first.")
        return 1

    actual = json.loads(args.actual.read_text(encoding="utf-8"))
    report = score_documents(expected, actual)
    print(format_report(report))

    gate = report.gate_status()
    if gate["passed"] is None:
        # Enough to score, not enough to decide. Still not a failure.
        print()
        print("Exit Gate 7 cannot be decided on this sample. Exiting 0.")
        return 0
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
