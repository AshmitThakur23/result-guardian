"""Phase 8 — retrieval, generation, and the verifier that guards both.

``verify.py`` deliberately imports nothing that can reach NODE B. A guard
implemented with the thing it guards against is not a guard, and keeping the
module free of that dependency is a structural check on 8.5's own rule:
**plain code, no AI**.
"""
