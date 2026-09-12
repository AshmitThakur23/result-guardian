# Clinical validation of the rule engine

> **Status: 🔴 NOT VALIDATED. No clinician has reviewed any part of this.**
>
> **Exit Gate 3 is OPEN and cannot be closed by anything in this repository.**

---

## The one-sentence version

The rule engine is built, tested and running; **nobody with clinical
authority has looked at what it decides.** Until a clinician walks the gold
set, every severity this system produces is an engineering guess about
medicine.

---

## What Phase 3.8 asks for

From [`docs/build/phase-03-clinical-rule-engine.md`](build/phase-03-clinical-rule-engine.md):

- [ ] Build a gold set: **100+ real anonymised results** spanning all three rule types
- [x] Store as `tests/fixtures/gold_set.yaml` with expected severity
- [x] Run as a pytest parametrised suite — *this becomes your regression net forever*
- [ ] Sit with a clinician, walk every disagreement, **tune thresholds, not code**
- [ ] Record final agreement rate here

And from **Exit Gate 3**:

- [ ] 🔴 Gold set passes at **≥95% agreement with the clinician**
- [x] ✅ Culture-vs-discharge-drug case demonstrably produces **CRITICAL**
- [x] ✅ A negated sentence (*"no evidence of malignancy"*) does **not** produce a flag
- [x] ✅ A contaminant culture produces **FOLLOW_UP**, not CRITICAL, and does **not** auto-close

The three ticked gate clauses are **mechanical** claims — the engine behaves
that way, and there is a test that fails if it stops. The unticked one is a
claim about a **clinician's judgement**, and it is the reason this gate stays
shut.

---

## The agreement rate

**There is no agreement rate.** Not "pending", not "provisional" — the number
does not exist, because the comparison it measures has not happened.

| | |
|---|---|
| Clinician who reviewed the set | **nobody** |
| Date reviewed | **never** |
| Real anonymised results in the set | **0** |
| Synthetic engineering cases in the set | **54** |
| Agreement rate | **does not exist** |

The test suite refuses to compute a percentage while the set is synthetic —
see `test_no_agreement_rate_is_reported_for_synthetic_cases` in
[`api/tests/test_gold_set.py`](../api/tests/test_gold_set.py). A percentage
next to the words "gold set" reads as clinical agreement whatever the prose
around it says, so the machinery that would produce one is absent rather than
disabled.

A second test, `test_the_gold_set_does_not_claim_clinician_validation`, fails
if `clinician_validated` is flipped to `true` in the fixture without a named
reviewer, a date, and every case ceasing to be marked synthetic. Flipping a
flag is a one-character edit; booking a clinician is not.

---

## What *has* been validated, and what that is worth

**54 synthetic cases pass**, spanning Rule A (16), Rule B (22), Rule C (13)
and mixed reports (3). Every one runs the whole path — content saved through
the real intake endpoint, classified by the real orchestrator, against the
real configuration tables.

That makes the gold set a genuine **regression net**: change a rule and the
cases that change answer will say so. It is not evidence that the answers are
clinically right. The expectations were written by the same people who wrote
the rules, which is exactly the circularity a clinician exists to break.

The thresholds the cases are graded against are the **development
placeholders** from [`api/scripts/seed_rules_dev.py`](../api/scripts/seed_rules_dev.py),
every one of which carries:

```
source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'
```

They are plausible-shaped round numbers. They are not any hospital's critical
value list, and the build plan is explicit that they must be replaced:
*"seed from your hospital's own critical value list, not from the internet."*

---

## What a hospital has to do to close this gate

1. **Replace the panic thresholds.** Find the placeholders with
   `SELECT * FROM panic_thresholds WHERE source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'`
   and replace them with the hospital's own SOP values. Same for
   `clinical_keywords`, `negation_patterns`, `antibiotic_synonyms` (the local
   brand names matter) and `mdro_rules`.
2. **Export 100+ real anonymised results** spanning all three rule types.
3. **Write them into the gold set** using the schema in
   [`clinical-validation-protocol.md` §1](clinical-validation-protocol.md) —
   `provenance: real_anonymised`, and the clinician's judgement recorded in a
   `clinician:` block **separate from** the engine's answer.

   ⚠️ Not the current single `expected_severity` field. If the clinician's
   judgement and the pass/fail expectation are the same field, then "fixing" a
   failing case by editing it makes the agreement rate 100% by construction.
   The protocol explains this and the schema change it requires.
4. **Run `pytest tests/test_gold_set.py`.** Every failure is a *disagreement*.
5. **Walk every disagreement with the clinician.** **Tune the configuration
   tables, not the rule code.** A rule edited to satisfy one case is a rule
   nobody can predict; a threshold moved in a table is a change an admin can
   see, audit and revert.
6. **Record the outcome here** — the clinician's name, the date, the number of
   real results, and the agreement rate — and set `clinician_validated: true`
   in the fixture.

Until step 6, this document says what it says now.

---

## Why this is written so bluntly

A system that tells a doctor *"this result is critical"* is making a clinical
claim. If the basis for that claim is an engineer's guess and the
documentation implies otherwise, the failure is not a bug — it is a hospital
trusting something that was never checked, which is the exact failure Result
Guardian exists to prevent, pointed at itself.

Two rules from the project's own governing documents apply directly:

- **RULE 1** — the safety property never depends on AI. It does not here: the
  engine is table lookups and comparisons, no model, no NODE B. But *not
  depending on AI* is not the same as *being clinically correct*.
- **THE ONE RULE** — a later phase must never break an earlier one. Phases 0–2
  guarantee that no result is *lost*. Phase 3 decides how loudly to say so. If
  Phase 3's judgement is merely wrong, the case is still tracked, still
  recorded, still visible, and still carries a severity somebody can overrule.
  That is why an unvalidated rule engine is safe to ship *behind* the
  tracking, and would not be safe to ship *instead of* it.

  Worth stating precisely, because the audit found it was not automatic: once
  a result arrives, intake supersedes the case's `result_due` timer, so **no
  timer is left to fire**. A classification that failed outright therefore
  left the case silent rather than degraded. Finding A3 fixed that — a rule
  that cannot answer now yields FOLLOW_UP, so the case is flagged for a human
  instead of going quiet. The guarantee holds because it was repaired, not
  because it was free.

---

## Related

- **The procedure for actually doing this:** [`clinical-validation-protocol.md`](clinical-validation-protocol.md) — import schema, de-identification, reviewer fields, how the agreement rate will be computed once it is genuinely earned
- Gold set fixture: [`api/tests/fixtures/gold_set.yaml`](../api/tests/fixtures/gold_set.yaml)
- Harness: [`api/tests/test_gold_set.py`](../api/tests/test_gold_set.py)
- Development placeholders: [`api/scripts/seed_rules_dev.py`](../api/scripts/seed_rules_dev.py)
- Phase doc: [`docs/build/phase-03-clinical-rule-engine.md`](build/phase-03-clinical-rule-engine.md)
