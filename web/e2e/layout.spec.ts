/**
 * Layout, measured in a real browser. The gate that did not exist.
 *
 * ## Why this file had to be written
 *
 * On 2026-09-15 the worklist shipped with its Patient and Test columns
 * collapsed to zero width, their text spilling across the Severity column —
 * the header rendered as `DEVERITY` and a row as `NSEEDt-1094sified`. Every
 * gate was green at the time: `tsc` 0, 187 vitest tests, `build` 0.
 *
 * **vitest runs in jsdom, and jsdom performs no layout.** It has no widths, no
 * overflow, no stacking — `getBoundingClientRect()` returns zeroes for
 * everything, so a column that is genuinely zero pixels wide is
 * indistinguishable from one that is fine. No unit test could ever have caught
 * this, and none ever will.
 *
 * The one thing that rendered in a real browser, `screenshots.spec.ts`, says of
 * itself *"Not assertions — a review aid"* and is skipped unless `RG_SHOTS=1`.
 * So the project had **no test anywhere capable of failing on a layout
 * defect**, and the first thing that noticed was a human looking at a picture.
 *
 * ## What this asserts, and why these three
 *
 * 1. **Every column has width.** The direct check for the observed bug.
 * 2. **No cell's content overflows its own box.** `scrollWidth > clientWidth`
 *    is what "text spilling into the next column" actually is. This is the more
 *    general assertion: it catches a column that is merely *too narrow* as well
 *    as one that is zero.
 * 3. **The sticky header stays put.** `position: sticky` fails silently — an
 *    `overflow: hidden` ancestor or a missing height constraint and it simply
 *    does not stick, with no error anywhere.
 *
 * Both themes, because only one palette is on screen at a time and a layout can
 * differ between them (a longer word, a different font fallback).
 *
 * ## ★ And several viewports, which is the whole point
 *
 * The first draft of this file checked only Playwright's default 1280x720 —
 * **and passed**, against markup a human could plainly see was broken. Column
 * squeeze is width-dependent: with the fixed columns summing to 672px, the two
 * `auto` columns divide whatever is left, so they are comfortable at 1440,
 * tight at 1280, and collapse below about 1150. 1280 is the one width at which
 * this bug hides.
 *
 * A layout test pinned to a single viewport is barely a test. These widths are
 * chosen to straddle the failure: a wide monitor, Playwright's default, a small
 * laptop, and a narrow window.
 *
 * ⚠️ Kept deliberately structural. This asserts that the table *has* a shape,
 * never which shape — a test that pins exact pixel widths would fail on every
 * legitimate design change and get deleted within a week.
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// @ts-expect-error -- plain ESM helper
import { query, seedGatedEncounter } from "./seed.mjs";
import { apiAuth, signIn } from "./auth";

/**
 * Open the worklist with rows on screen.
 *
 * Signs in as the **doctor** who owns the seeded cases and clears "Only mine"
 * anyway. The table only renders when `rows.length > 0`, so an empty worklist
 * makes every assertion below vacuous — and the first draft of this file did
 * exactly that: it timed out waiting for a region that never rendered, which
 * reads as a layout failure and is not one.
 */
async function openWorklistWithRows(page: Page, doctorCode: string) {
  await signIn(page, doctorCode);
  await page.goto("/worklist");

  const onlyMine = page.getByLabel("Only mine");
  if (await onlyMine.isChecked().catch(() => false)) await onlyMine.uncheck();

  await page.getByRole("region", { name: "Open flags" }).waitFor({ timeout: 30_000 });
  const rows = await page.locator("table tbody tr").count();
  expect(rows, "the worklist rendered no rows, so nothing was measured").toBeGreaterThan(0);
}

interface Seed {
  adminCode: string;
  doctorCode: string;
  doctorId: string;
  encounterId: string;
}

test.describe.configure({ timeout: 120_000 });

async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.evaluate((value) => {
    const root = document.documentElement;
    root.classList.toggle("dark", value === "dark");
    root.classList.toggle("light", value === "light");
    try {
      localStorage.setItem("rg-theme", value);
    } catch {
      /* private window — the classes are what matter */
    }
  }, theme);
  // One frame for the custom properties and any reflow to settle.
  await page.waitForTimeout(150);
}

/** Discharge so the worklist has rows with real patient names and tests. */
async function seedRows(request: import("@playwright/test").APIRequestContext) {
  const data = seedGatedEncounter() as Seed;
  const headers = await apiAuth(request, data.doctorCode);
  const readiness = await (
    await request.get(`/api/encounters/${data.encounterId}/discharge-readiness`, {
      headers,
    })
  ).json();
  await request.post(`/api/encounters/${data.encounterId}/discharge-contracts`, {
    headers,
    data: {
      contracts: (readiness.blocking_orders as { order_id: string }[]).map((o) => ({
        order_id: o.order_id,
        responsible_doctor_id: data.doctorId,
        expected_by: new Date(Date.now() + 48 * 3600_000).toISOString(),
      })),
    },
  });
  expect(
    (await request.post(`/api/encounters/${data.encounterId}/discharge`, { headers }))
      .status(),
  ).toBe(200);
  return data;
}

/** Widths chosen to straddle the collapse, not to be tidy round numbers. */
const VIEWPORTS = [
  { name: "wide monitor", width: 1600, height: 900 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "small laptop", width: 1100, height: 800 },
  { name: "narrow window", width: 960, height: 800 },
  // Below the width at which the fixed columns (672px in total) leave anything
  // for the two elastic ones. Measured here: Patient and Test drop to 37px and
  // the patient link overhangs its cell by 58px. This is also what a ~1050px
  // window looks like at 150% browser zoom, which is how it was first seen.
  { name: "zoomed / very narrow", width: 780, height: 800 },
  // The Android app loads this same page over the LAN, so a phone is a real
  // deployment target, not a nice-to-have.
  { name: "phone", width: 390, height: 844 },
];

test("worklist columns all have width, and nothing spills", async ({
  page,
  request,
}) => {
  const data = await seedRows(request);
  await openWorklistWithRows(page, data.doctorCode);

  const problems: string[] = [];

  for (const viewport of VIEWPORTS) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    const where = `${viewport.width}px ${theme}`;

    // ── 1. every column has a width ──────────────────────────────
    const headers = await page
      .locator("table thead th")
      .evaluateAll((nodes) =>
        nodes.map((n) => ({
          label: (n.textContent ?? "").trim() || "(select)",
          width: Math.round(n.getBoundingClientRect().width),
        })),
      );

    expect(headers.length, "no table header was found at all").toBeGreaterThan(4);
    for (const header of headers) {
      // 24px is below any usable column and well under the narrowest real one
      // (`w-10` = 40px), so this cannot fire on a legitimately tight column.
      if (header.width < 24) {
        problems.push(
          `${where}: column "${header.label}" is ${header.width}px wide — collapsed`,
        );
      }
    }

    // ── 2. ★ nothing paints outside its own cell ─────────────────
    //
    // Compared as **geometry**, not via `scrollWidth`. A `<td>` is
    // `overflow: visible`, and for such a box browsers routinely report
    // `scrollWidth === clientWidth` even while a child paints well past the
    // edge. The first draft of this check used `scrollWidth - clientWidth` and
    // passed against markup that was visibly broken — measured at 780px the
    // Patient cell is 37px wide with its link overhanging by 58px, and
    // `scrollWidth` reported nothing at all.
    //
    // Measuring each child's rect against its cell's rect is what actually
    // detects a spill, because that is literally what a spill is.
    const spills = await page.locator("table th, table td").evaluateAll((cells) => {
      const out: { text: string; over: number }[] = [];
      for (const cell of cells) {
        const box = cell.getBoundingClientRect();
        if (box.width === 0) continue;
        for (const child of Array.from(cell.querySelectorAll("*"))) {
          const kid = (child as HTMLElement).getBoundingClientRect();
          if (kid.width === 0) continue;
          const over = Math.round(
            Math.max(kid.right - box.right, box.left - kid.left),
          );
          // 1px of sub-pixel rounding is not a spill.
          if (over > 1) {
            out.push({ text: (child.textContent ?? "").trim().slice(0, 30), over });
            break; // one report per cell is enough
          }
        }
      }
      return out;
    });
      for (const spill of spills) {
        problems.push(
          `${where}: cell "${spill.text}" overflows its column by ${spill.over}px`,
        );
      }
    }
  }

  expect(problems, `layout defects:\n  ${problems.join("\n  ")}`).toEqual([]);
});

test("the sticky table header stays put while the rows scroll", async ({
  page,
  request,
}) => {
  const data = await seedRows(request);
  await openWorklistWithRows(page, data.doctorCode);
  const region = page.getByRole("region", { name: "Open flags" });

  const scrollable = await region.evaluate(
    (el) => el.scrollHeight > el.clientHeight + 4,
  );
  // With only a handful of seeded rows the region may not scroll at all, and a
  // sticky assertion against a non-scrolling container proves nothing. Say so
  // rather than passing quietly.
  test.skip(
    !scrollable,
    "the worklist is shorter than its container, so there is nothing to scroll",
  );

  const headerTop = async () =>
    Math.round(
      (await page.locator("table thead th").first().boundingBox())?.y ?? -1,
    );

  const before = await headerTop();
  await region.evaluate((el) => el.scrollBy(0, 200));
  await page.waitForTimeout(120);
  const after = await headerTop();

  // `position: sticky` fails silently — an `overflow: hidden` ancestor and it
  // simply does not stick, with nothing logged anywhere.
  expect(
    Math.abs(after - before),
    `the header moved ${Math.abs(after - before)}px while scrolling; sticky is not working`,
  ).toBeLessThanOrEqual(2);
});

test("the case detail screen has no spilling cells either", async ({
  page,
  request,
}) => {
  const data = await seedRows(request);
  const caseId = query(
    `SELECT id FROM pending_cases WHERE encounter_id = '${data.encounterId}' LIMIT 1`,
  );
  expect(caseId, "discharge produced no pending case").toBeTruthy();

  await signIn(page, data.doctorCode);
  await page.goto(`/cases/${caseId}`);
  await page.getByRole("heading", { level: 1 }).first().waitFor();

  const problems: string[] = [];
  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    const spills = await page
      .locator("main th, main td, main dd, main dt")
      .evaluateAll((nodes) =>
        nodes
          .map((n) => ({
            text: (n.textContent ?? "").trim().slice(0, 30),
            over: n.scrollWidth - n.clientWidth,
          }))
          .filter((c) => c.over > 1),
      );
    for (const spill of spills) {
      problems.push(`${theme}: "${spill.text}" overflows by ${spill.over}px`);
    }
  }

  expect(problems, `layout defects:\n  ${problems.join("\n  ")}`).toEqual([]);
});
