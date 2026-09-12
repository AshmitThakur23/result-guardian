import { describe, expect, it } from "vitest";

import {
  checkExpectedBy,
  formatDeadline,
  inputValueToIso,
  isoToInputValue,
  minExpectedByInputValue,
  outstandingFor,
} from "./datetime";

const NOW = new Date("2026-03-14T10:00:00Z");

describe("wire <-> input conversion", () => {
  it("round-trips through the browser's own timezone", () => {
    const input = isoToInputValue(NOW.toISOString());
    expect(inputValueToIso(input)).toBe(NOW.toISOString());
  });

  it("always produces an aware timestamp, which the API requires", () => {
    // A naive value is rejected outright by `AwareDatetime` on the server, so
    // this is the single most important property of the whole module.
    const iso = inputValueToIso("2026-03-14T18:00");
    expect(iso).toMatch(/(Z|[+-]\d{2}:\d{2})$/);
  });

  it("returns empty rather than a bogus field value for junk input", () => {
    expect(isoToInputValue("not-a-date")).toBe("");
    expect(isoToInputValue(null)).toBe("");
    expect(inputValueToIso("not-a-date")).toBeNull();
    expect(inputValueToIso("")).toBeNull();
  });
});

describe("expected_by validation mirrors the server", () => {
  it("rejects an empty field", () => {
    expect(checkExpectedBy("", NOW)).toBe("missing");
  });

  it("rejects anything inside the one-hour lead time", () => {
    const in30min = isoToInputValue(new Date(NOW.getTime() + 30 * 60000).toISOString());
    expect(checkExpectedBy(in30min, NOW)).toBe("too_soon");
  });

  it("accepts the hour boundary itself", () => {
    expect(checkExpectedBy(minExpectedByInputValue(NOW), NOW)).toBeNull();
  });

  it("rejects beyond the 30-day contract horizon", () => {
    const day31 = isoToInputValue(
      new Date(NOW.getTime() + 31 * 24 * 3600_000).toISOString(),
    );
    expect(checkExpectedBy(day31, NOW)).toBe("too_far");
  });

  it("accepts a normal deadline", () => {
    const tomorrow = isoToInputValue(
      new Date(NOW.getTime() + 24 * 3600_000).toISOString(),
    );
    expect(checkExpectedBy(tomorrow, NOW)).toBeNull();
  });
});

describe("display", () => {
  it("formats a deadline the way step 3 reads it aloud", () => {
    expect(formatDeadline(NOW.toISOString())).toMatch(/14 Mar 2026, \d{1,2}:\d{2} (AM|PM)/);
  });

  it("never renders 'Invalid Date' at a doctor", () => {
    expect(formatDeadline("rubbish")).toBe("—");
    expect(formatDeadline(null)).toBe("—");
  });

  it("says how long an order has been outstanding in plain words", () => {
    expect(outstandingFor(new Date(NOW.getTime() - 30 * 60000).toISOString(), NOW)).toBe(
      "less than an hour",
    );
    expect(outstandingFor(new Date(NOW.getTime() - 3600_000).toISOString(), NOW)).toBe(
      "1 hour",
    );
    expect(
      outstandingFor(new Date(NOW.getTime() - 50 * 3600_000).toISOString(), NOW),
    ).toBe("2 days");
  });
});
