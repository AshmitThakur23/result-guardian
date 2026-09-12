/**
 * Leave no residue, start from none, and make sure the rules exist.
 *
 * Cleaning *before* the run as well as after is what makes the suite
 * repeatable: a previous crashed run would otherwise leave doctors behind and
 * make the next run's searches ambiguous.
 *
 * Seeding the rule configuration is here for a reason worth writing down. The
 * Phase 3 specs assert real severities, and every threshold, keyword and
 * antibiotic synonym that produces one lives in a **table** — that is the
 * point of Phase 3.2. So an empty configuration does not fail loudly; it
 * quietly grades everything as unclassifiable, and the specs fail with
 * "element not found" while the product looks fine.
 *
 * And the tables do empty. `api/tests/test_migration_round_trip.py` downgrades
 * to base and back, which drops and recreates them. Running the Python suite
 * between two Playwright runs was enough to break the second one — which is
 * how this was found.
 */
// @ts-expect-error -- plain ESM helper, deliberately untyped
import { cleanupE2EData, seedRuleConfig } from "./seed.mjs";

export default function globalSetup(): void {
  cleanupE2EData();
  seedRuleConfig();
}
