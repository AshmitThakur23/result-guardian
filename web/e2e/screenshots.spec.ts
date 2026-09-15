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

import { expect, test } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { seedGatedEncounter } from "./seed.mjs";
import { apiAuth, signIn } from "./auth";

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
    test(`every screen, ${theme}`, async ({ page, request }) => {
      // ★ Discharge first, and sign in as the doctor who owns the result.
      //
      // This used to sign in as an admin and go straight to /worklist, whose
      // default filter is "only mine" -- so every worklist screenshot ever taken
      // was of the EMPTY STATE. A review aid that photographs a blank page is
      // how a table with collapsed columns went unnoticed through four agents
      // and a green suite.
      const data = seedGatedEncounter() as {
        adminCode: string;
        doctorCode: string;
        doctorId: string;
        encounterId: string;
      };
      const headers = await apiAuth(request, data.doctorCode);
      const readiness = await (
        await request.get(
          `/api/encounters/${data.encounterId}/discharge-readiness`,
          { headers },
        )
      ).json();
      await request.post(
        `/api/encounters/${data.encounterId}/discharge-contracts`,
        {
          headers,
          data: {
            contracts: (
              readiness.blocking_orders as { order_id: string }[]
            ).map((o) => ({
              order_id: o.order_id,
              responsible_doctor_id: data.doctorId,
              expected_by: new Date(Date.now() + 48 * 3600_000).toISOString(),
            })),
          },
        },
      );
      await request.post(`/api/encounters/${data.encounterId}/discharge`, {
        headers,
      });

      await signIn(page, data.doctorCode);

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

        // Show every row, not just this doctor's, so the worklist shot has
        // something in it. A photograph of an empty state teaches nobody
        // anything about the table.
        if (screen.path === "/worklist") {
          const onlyMine = page.getByLabel("Only mine");
          if (await onlyMine.isChecked().catch(() => false)) {
            await onlyMine.uncheck();
            await page.waitForTimeout(400);
          }
          expect(
            await page.locator("table tbody tr").count(),
            "the worklist screenshot would have been of an empty page",
          ).toBeGreaterThan(0);
        }
        await page.screenshot({
          path: `screenshots/${theme}-${screen.name}.png`,
          fullPage: true,
        });
      }
    });
  }
});
