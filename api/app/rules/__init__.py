"""The clinical rule engine. Phase 3.

    Goal: correct severity classification, fully deterministic, **no AI.**

That is RULE 1 made concrete. Nothing in this package imports a model, calls
NODE B, or infers anything. Every decision is a comparison against a value an
admin put in a table, and every decision carries a machine-readable
``reason_code`` saying which comparison produced it.

Three rules, picked by *content* rather than by report type:

* **A** — numeric analytes against reference ranges and panic thresholds
* **B** — cultures against the drugs the patient actually went home on
* **C** — narrative prose against a keyword list, with negation

``orchestrator.py`` runs whichever apply and takes the **maximum** severity.

The engine version is stamped on every classification. A threshold that
changes next month must not make last month's decision unexplainable.
"""

from __future__ import annotations

# Bumped whenever a rule's *behaviour* changes, never for a refactor. It is
# written onto every classification row, and `classifications` is unique on
# (result_id, engine_version) -- so a re-run under the same version is a
# replay and writes nothing, while a re-run under a new version is a
# genuinely different decision and is recorded alongside the old one.
ENGINE_VERSION = "3.0.0"

# The three severities, ordered. `max` over rule outputs is the whole
# combination strategy, so the ordering is the contract.
SEVERITY_NORMAL = "normal"
SEVERITY_FOLLOW_UP = "follow_up"
SEVERITY_CRITICAL = "critical"

SEVERITY_ORDER = (SEVERITY_NORMAL, SEVERITY_FOLLOW_UP, SEVERITY_CRITICAL)


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity)


def max_severity(*severities: str) -> str:
    """The worst of several rule outputs.

    Defaults to NORMAL only when nothing ran at all; a rule that declines to
    classify returns FOLLOW_UP, never NORMAL, because "I could not tell" and
    "it is fine" are different answers and only one of them is safe to
    auto-close on.
    """
    if not severities:
        return SEVERITY_NORMAL
    return max(severities, key=severity_rank)
