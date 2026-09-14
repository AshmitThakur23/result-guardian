"""Phase 7 — classification, extraction, normalisation and status handling.

The matcher (7.5) lives one level up in ``app/services/matching.py`` rather than
here, deliberately: it must never acquire an import path into an extraction
module that could one day reach a model. **AI never decides which patient a
result belongs to**, and keeping that file free of extraction's dependencies is
one small structural guard on the rule.
"""
