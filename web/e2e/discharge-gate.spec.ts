/**
 * The discharge gate, end to end, against the real stack.
 *
 * The vitest suite proves the component behaves. This proves the *product*
 * behaves: a real browser, the real bundle Caddy serves, the real API, and --
 * crucially -- assertions read back out of Postgres afterwards. A gate that
 * looks right on screen but writes no contract row is exactly the failure this
 * catches and jsdom cannot.
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// @ts-expect-error -- plain ESM helper, deliberately untyped
import { query, seedGatedEncounter } from "./seed.mjs";

interface Seed {
  encounterId: string;
  doctorId: string;
  unitHeadId: string;
  attendingName: string;
  otherName: string;
  unitHeadName: string;
  patientName: string;
  mrn: string;
  orderA: string;
  orderB: string;
}

function seed(): Seed {
  return seedGatedEncounter() as Seed;
}

function gateUrl(encounterId: string): string {
  return `/encounters/${encounterId}/discharge`;
}

/** A `datetime-local` value a fixed number of hours out, in the browser's zone. */
function inHours(hours: number): string {
  const at = new Date(Date.now() + hours * 3600_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(
    at.getHours(),
  )}:${pad(at.getMinutes())}`;
}

async function setExpectedBy(page: Page, orderId: string, value: string) {
  await page.locator(`#expected-${orderId}`).fill(value);
}

test.describe("the gate blocks", () => {
  test("lists every unowned investigation and offers no way past it", async ({ page }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));

    await expect(page.getByRole("heading", { name: data.patientName })).toBeVisible();
    await expect(page.getByText("Urine Culture")).toBeVisible();
    await expect(page.getByText("HbA1c")).toBeVisible();
    await expect(
      page.getByText("2 investigations have no one responsible for their results"),
    ).toBeVisible();

    // The product claim, asserted against the rendered page: no control
    // anywhere lets a doctor walk past an unassigned result.
    const labels = await page.getByRole("button").allInnerTexts();
    for (const label of labels) {
      expect(label).not.toMatch(
        /\b(skip|not required|dismiss|ignore|remind me later|discharge anyway|mark as done)\b/i,
      );
    }
  });

  test("refuses to reach step 3 while a row is incomplete", async ({ page }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));
    await page.getByRole("button", { name: "Assign responsibility" }).click();

    // HbA1c's turnaround has already elapsed, so its date is deliberately blank.
    await expect(page.locator(`#expected-${data.orderB}`)).toHaveValue("");
    await expect(page.getByText(/No usable default/)).toBeVisible();

    await page.getByRole("button", { name: "Review" }).click();
    await expect(page.getByText("1 row is incomplete")).toBeVisible();
    await expect(page.getByRole("heading", { name: /Step 3/ })).toHaveCount(0);
  });
});

test.describe("the gate lets a prepared discharge through", () => {
  test("assign, review, confirm — and the contracts exist in the database", async ({
    page,
  }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));

    await page.getByRole("button", { name: "Assign responsibility" }).click();

    // The attending doctor is the default owner on every row.
    await expect(page.getByRole("combobox").first()).toHaveValue(data.attendingName);

    await setExpectedBy(page, data.orderA, inHours(30));
    await setExpectedBy(page, data.orderB, inHours(30));
    await page.getByRole("button", { name: "Review" }).click();

    // Step 3 reads the assignments back as sentences.
    const review = page.getByRole("region", { name: /Step 3/ });
    await expect(review).toContainText(`Dr ${data.attendingName}`);
    await expect(review).toContainText("will review");
    await expect(review).toContainText("Urine Culture");
    await expect(review).toContainText("HbA1c");

    await page.getByRole("button", { name: "Confirm discharge" }).click();

    await expect(page.getByText(`${data.patientName} discharged`)).toBeVisible();
    await expect(page.getByText("Contract references")).toBeVisible();

    // What the screen says is not evidence. What the database holds is.
    expect(
      query(
        `SELECT count(*) FROM discharge_contracts WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
    expect(
      query(`SELECT status FROM encounters WHERE id = '${data.encounterId}'`),
    ).toBe("discharged");
    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
    expect(
      query(
        `SELECT count(DISTINCT responsible_doctor_id) FROM discharge_contracts ` +
          `WHERE encounter_id = '${data.encounterId}' ` +
          `AND responsible_doctor_id = '${data.doctorId}'`,
      ),
    ).toBe("1");
  });

  test("can be completed without ever touching the mouse", async ({ page }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));

    // Step 1 → step 2 by keyboard.
    await page.getByRole("button", { name: "Assign responsibility" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: /Step 2/ })).toBeVisible();

    // Reassign the first row to a different doctor with arrows and Enter only.
    const firstDoctor = page.getByRole("combobox").first();
    await firstDoctor.focus();
    await page.keyboard.type("Ravi");
    await expect(page.getByRole("option", { name: new RegExp(data.otherName) })).toBeVisible();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(firstDoctor).toHaveValue(data.otherName);

    await setExpectedBy(page, data.orderA, inHours(30));
    await setExpectedBy(page, data.orderB, inHours(30));

    await page.getByRole("button", { name: "Review" }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Confirm discharge" }).focus();
    await page.keyboard.press("Enter");

    await expect(page.getByText(`${data.patientName} discharged`)).toBeVisible();

    // The keyboard choice actually reached the database, not just the screen.
    expect(
      query(
        `SELECT count(*) FROM discharge_contracts WHERE order_id = '${data.orderA}' ` +
          `AND responsible_doctor_id <> '${data.doctorId}'`,
      ),
    ).toBe("1");
  });
});

test.describe("a draft survives the ward PC", () => {
  test("a real page reload restores the step and the choices", async ({ page }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));
    await page.getByRole("button", { name: "Assign responsibility" }).click();

    const firstDoctor = page.getByRole("combobox").first();
    await firstDoctor.click();
    await page.keyboard.type("Ravi");
    await page.getByRole("option", { name: new RegExp(data.otherName) }).click();
    await setExpectedBy(page, data.orderB, inHours(30));

    await page.reload();

    await expect(page.getByRole("region", { name: /Step 2/ })).toBeVisible();
    await expect(page.getByRole("combobox").first()).toHaveValue(data.otherName);
    await expect(page.locator(`#expected-${data.orderB}`)).not.toHaveValue("");
  });

  test("the draft is gone once the discharge completes", async ({ page }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));
    await page.getByRole("button", { name: "Assign responsibility" }).click();
    await setExpectedBy(page, data.orderA, inHours(30));
    await setExpectedBy(page, data.orderB, inHours(30));
    await page.getByRole("button", { name: "Review" }).click();
    await page.getByRole("button", { name: "Confirm discharge" }).click();
    await expect(page.getByText(`${data.patientName} discharged`)).toBeVisible();

    const stored = await page.evaluate(
      (id) => sessionStorage.getItem(`rg.discharge-draft.${id}`),
      data.encounterId,
    );
    expect(stored).toBeNull();
  });
});

test.describe("the override path", () => {
  test("records the reason and still tracks every investigation", async ({ page }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));

    await page.getByRole("button", { name: "Override the gate" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Nothing stops being tracked")).toBeVisible();

    const confirm = dialog.getByRole("button", { name: "Override and discharge" });
    await expect(confirm).toBeDisabled();

    await dialog.getByLabel("Reason").selectOption("patient_lama");
    await dialog
      .getByLabel("What happened?")
      .fill("Patient left the ward against advice before results returned.");
    await dialog.getByLabel("Overriding doctor").click();
    await page.keyboard.type("Asha");
    await page.getByRole("option", { name: new RegExp(data.attendingName) }).click();

    await expect(confirm).toBeEnabled();
    await confirm.click();

    await expect(page.getByText(/discharged with the gate overridden/)).toBeVisible();
    await expect(page.getByText(/Bypassed and flagged to the unit head/)).toBeVisible();

    // The safety property: an override is not a dismissal.
    expect(
      query(
        `SELECT count(*) FROM discharge_overrides WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("2");
    expect(
      query(
        `SELECT count(*) FROM pending_cases WHERE encounter_id = '${data.encounterId}' ` +
          `AND current_owner_id = '${data.unitHeadId}'`,
      ),
    ).toBe("2");
    expect(
      query(
        `SELECT DISTINCT reason_code FROM discharge_overrides ` +
          `WHERE encounter_id = '${data.encounterId}'`,
      ),
    ).toBe("patient_lama");
  });

  test("can be abandoned without discharging anything", async ({ page }) => {
    const data = seed();
    await page.goto(gateUrl(data.encounterId));

    await page.getByRole("button", { name: "Override the gate" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Go back and assign" })
      .click();

    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(
      query(`SELECT status FROM encounters WHERE id = '${data.encounterId}'`),
    ).toBe("active");
  });
});

test.describe("degradation", () => {
  test("a missing encounter is reported, not guessed at", async ({ page }) => {
    await page.goto("/encounters/01900000-0000-7000-8000-000000000000/discharge");
    await expect(page.getByText("Encounter not found")).toBeVisible();
    await expect(page.getByRole("button", { name: "Confirm discharge" })).toHaveCount(0);
  });

  test("a malformed encounter id never reaches the server", async ({ page }) => {
    await page.goto("/encounters/not-a-uuid/discharge");
    await expect(page.getByText("That is not a valid encounter reference")).toBeVisible();
  });
});
