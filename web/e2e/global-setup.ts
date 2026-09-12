/**
 * Leave no residue, and start from none.
 *
 * Cleaning *before* the run as well as after is what makes the suite
 * repeatable: a previous crashed run would otherwise leave doctors behind and
 * make the next run's searches ambiguous.
 */
// @ts-expect-error -- plain ESM helper, deliberately untyped
import { cleanupE2EData } from "./seed.mjs";

export default function globalSetup(): void {
  cleanupE2EData();
}
