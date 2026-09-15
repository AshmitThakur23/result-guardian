/**
 * WCAG contrast, measured rather than asserted. Accessibility gate.
 *
 * ## Why this exists
 *
 * The palette is written in OKLCH and reasoned about carefully in
 * `src/styles/tokens.css` — but reasoning about a colour space is not the same
 * as measuring the ratio, and on 2026-09-15 an audit found real failures that
 * a careful-sounding docstring had been covering:
 *
 *   * `bg-brand text-white` on the primary button. In **dark** mode
 *     `--rg-brand` is `oklch(70% 0.14 255)` — a mid-light blue — and white on
 *     it measures well under 4.5:1. The same defect sat on the danger button
 *     and on the ExplainPanel's citation ordinal.
 *
 * Those were fixed by swapping `text-white` for `text-ink-inverse`, which
 * flips with the theme. This file is what stops them coming back.
 *
 * ## Why no axe-core, no dependency
 *
 * The browser already does the hard part. `getComputedStyle` resolves
 * `oklch()` to `rgb()`, so the conversion this would otherwise need a colour
 * library for is free. The WCAG relative-luminance formula is six lines. A new
 * dependency to compute six lines of arithmetic would be a worse trade.
 *
 * ## Why both themes, explicitly
 *
 * A token system has two palettes and only one of them is on screen at a time.
 * A contrast test that runs in whichever theme the machine happened to be in
 * is testing the weather. Both are forced here.
 *
 * ## ⚠️ Colours must be read through a canvas, not a regex
 *
 * The first version of this file parsed `getComputedStyle(...).color` with an
 * `rgb()` regex and measured **zero elements** — because Chromium returns
 * `oklch(0.99 0 0)`, keeping the colour in its own space per CSS Color 4.
 * `ctx.fillStyle` round-trips it as `oklch()` too. Every colour parse returned
 * null, every element was skipped, and the suite would have reported a
 * confident green over nothing at all.
 *
 * The only thing that caught it was the `checked` count assertion at the end,
 * which is why that assertion is not optional: **a test that measures nothing
 * must fail, not pass.**
 *
 * Painting into a 1x1 canvas and reading the pixel back forces the conversion
 * to sRGB for any CSS colour the browser can render, whatever space it is
 * authored in.
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// @ts-expect-error -- plain ESM helper
import { seedGatedEncounter } from "./seed.mjs";
import { signIn } from "./auth";

/**
 * Force a theme.
 *
 * Both classes are set explicitly rather than toggling one: `tokens.css` themes
 * on `prefers-color-scheme` **as well as** on a class, so with neither class
 * present the palette follows the machine — and a contrast test that runs in
 * whichever theme the CI box happens to prefer is testing the weather.
 */
async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.evaluate((value) => {
    const root = document.documentElement;
    root.classList.toggle("dark", value === "dark");
    root.classList.toggle("light", value === "light");
    try {
      localStorage.setItem("rg-theme", value);
    } catch {
      /* private window — the classes above are what actually matter */
    }
  }, theme);
}

interface Failure {
  theme: string;
  label: string;
  measured: number;
  text: string;
}

/**
 * Measure every visible text node on the page, in one pass inside the browser.
 *
 * Done in a single `evaluate` rather than element-by-element over the wire:
 * a page has hundreds of text nodes and a round trip each would take minutes.
 *
 * 4.5:1 is WCAG AA for body text; 3:1 is the large-text allowance, applied only
 * where the computed size genuinely qualifies (>= 24px, or >= 18.66px and
 * bold). Size and weight are read from the DOM, never assumed from a class.
 */
async function auditPage(
  page: Page,
  theme: string,
): Promise<{ checked: number; failures: Failure[] }> {
  return page.evaluate((themeName) => {
    // 1x1 canvas: the only reliable way to resolve `oklch()` (or any other
    // CSS colour space) to sRGB bytes. `getComputedStyle` hands back the
    // authored space, and so does `ctx.fillStyle`.
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    const cache = new Map<string, number[] | null>();

    function toRgb(css: string): number[] | null {
      if (cache.has(css)) return cache.get(css)!;
      let out: number[] | null = null;
      try {
        // A fully transparent colour must not be treated as a real background.
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = "#000";
        ctx.fillStyle = css;
        ctx.fillRect(0, 0, 1, 1);
        const d = ctx.getImageData(0, 0, 1, 1).data;
        out = d[3] === 0 ? null : [d[0], d[1], d[2]];
      } catch {
        out = null;
      }
      cache.set(css, out);
      return out;
    }

    const luminance = ([r, g, b]: number[]) => {
      const ch = (c: number) => {
        const v = c / 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
    };
    const ratio = (fg: number[], bg: number[]) => {
      const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
      return (hi + 0.05) / (lo + 0.05);
    };

    /** The nearest ancestor that actually paints a background. */
    function backgroundOf(el: Element): number[] | null {
      let node: Element | null = el;
      while (node) {
        const rgb = toRgb(getComputedStyle(node).backgroundColor);
        if (rgb) return rgb;
        node = node.parentElement;
      }
      return toRgb(getComputedStyle(document.body).backgroundColor);
    }

    const failures: {
      theme: string;
      label: string;
      measured: number;
      text: string;
    }[] = [];
    let checked = 0;

    for (const el of Array.from(document.querySelectorAll("body *"))) {
      // Only elements with their own text; a wrapper inherits its child's.
      const own = Array.from(el.childNodes)
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => (n.textContent ?? "").trim())
        .join(" ")
        .trim();
      if (!own) continue;

      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;

      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      if (Number(cs.opacity) === 0) continue;

      const fg = toRgb(cs.color);
      const bg = backgroundOf(el);
      if (!fg || !bg) continue;

      const size = parseFloat(cs.fontSize);
      const weight = Number(cs.fontWeight) || 400;
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      const threshold = large ? 3 : 4.5;
      const measured = ratio(fg, bg);

      checked += 1;
      if (measured < threshold) {
        failures.push({
          theme: themeName,
          label: `${el.tagName.toLowerCase()} ${Math.round(size)}px need ${threshold}`,
          measured: Math.round(measured * 100) / 100,
          text: own.slice(0, 40),
        });
      }
    }
    return { checked, failures };
  }, theme);
}

test.describe.configure({ timeout: 120_000 });

test("severity badges and buttons clear WCAG AA in both themes", async ({
  page,
  request,
}) => {
  const data = seedGatedEncounter() as {
    adminCode: string;
    doctorCode: string;
    doctorId: string;
    encounterId: string;
  };

  // A real worklist, so the badges and buttons measured are the ones a
  // clinician actually sees rather than a storybook rendering of them.
  const { apiAuth } = await import("./auth");
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
  await request.post(`/api/encounters/${data.encounterId}/discharge`, { headers });

  // The **doctor** who owns the seeded cases, and "Only mine" cleared anyway.
  // Signed in as an admin the worklist is empty, the table never renders, and
  // there is almost nothing to measure — which is what the `checked` guard
  // below caught on the first run. A contrast audit over a blank page is the
  // most dangerous kind of green.
  await signIn(page, data.doctorCode);
  await page.goto("/worklist");
  await page.getByRole("heading", { level: 1 }).first().waitFor();

  const onlyMine = page.getByLabel("Only mine");
  if (await onlyMine.isChecked().catch(() => false)) await onlyMine.uncheck();
  await page
    .getByRole("region", { name: "Open flags" })
    .waitFor({ timeout: 30_000 });

  const failures: Failure[] = [];
  let checked = 0;

  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    // One frame for the new custom-property values to resolve.
    await page.waitForTimeout(150);
    const result = await auditPage(page, theme);
    checked += result.checked;
    failures.push(...result.failures);
  }

  // Guard against the test silently measuring nothing — an empty worklist or a
  // changed selector would otherwise report a confident green.
  expect(checked, "no text was measured; the audit is not running").toBeGreaterThan(20);

  expect(
    failures,
    `WCAG AA contrast failures:\n${failures
      .map((f) => `  ${f.theme.padEnd(5)} ${f.measured}:1  ${f.label}  "${f.text}"`)
      .join("\n")}`,
  ).toEqual([]);
});
