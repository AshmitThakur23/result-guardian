// @ts-expect-error -- plain ESM helper, deliberately untyped
import { cleanupE2EData } from "./seed.mjs";

export default function globalTeardown(): void {
  cleanupE2EData();
}
