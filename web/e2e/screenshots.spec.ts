/**
 * Screenshots of every signed-in screen, light and dark.
 *
 * Not assertions — a review aid. Design work that is only ever described in a
 * commit message does not get looked at, and a contrast mistake is obvious in
 * a picture and invisible in a diff.
 *
 * Skipped by default so it never slows the real suite. Run it deliberately:
 *
 *     npx playwright test screenshots --grep-invert nothing
 *     RG_SHOTS=1 npx playwright test screenshots
 *
 * Output lands in `web/screenshots/`, which is git-ignored.
 */

import { test } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { seedGatedEncounter } from "./seed.mjs";
import { signIn } from "./auth";

const SHOOT = process.env.RG_SHOTS === "1";

const SCREENS: { path: string; name: string }[] = [
  { path: "/worklist", name: "worklist" },
  { path: "/patients", name: "patients" },
  { path: "/documents", name: "documents" },
  { path: "/reports", name: "reports" },
  { path: "/audit", name: "audit" },
  { path: "/admin", name: "admin" },
];

test.describe("screenshots", () => {
  test.skip(!SHOOT, "set RG_SHOTS=1 to capture");
  test.setTimeout(120_000);

  for (const theme of ["light", "dark"] as const) {
    test(`every screen, ${theme}`, async ({ page }) => {
      const data = seedGatedEncounter() as { adminCode: string };
      await signIn(page, data.adminCode);

      // Set the stored preference the way the toggle does, so the inline
      // script in index.html picks it up on the next load.
      await page.addInitScript(
        (value) => localStorage.setItem("rg-theme", value),
        theme,
      );

      for (const screen of SCREENS) {
        await page.goto(screen.path);
        // Let the queries settle so the shot is of content, not spinners.
        await page.waitForLoadState("networkidle").catch(() => undefined);
        await page.screenshot({
          path: `screenshots/${theme}-${screen.name}.png`,
          fullPage: true,
        });
      }
    });
  }
});
