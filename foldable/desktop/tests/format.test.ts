import { describe, expect, it } from "vitest";
import { formatBytes, formatEta, formatPercent, formatRelativeDay, formatSpeed, truncate } from "../src/renderer/lib/format";

describe("formatBytes", () => {
  it("formats with German decimal comma and 1024 base", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1,0 KB");
    expect(formatBytes(1536 * 1024)).toBe("1,5 MB");
    expect(formatBytes(1834426016)).toBe("1,7 GB");
    expect(formatBytes(4294967295)).toBe("4,0 GB");
    expect(formatBytes(2.5 * 1024 ** 4)).toBe("2,5 TB");
  });

  it("handles invalid input", () => {
    expect(formatBytes(null)).toBe("–");
    expect(formatBytes(undefined)).toBe("–");
    expect(formatBytes(-5)).toBe("–");
    expect(formatBytes(Number.NaN)).toBe("–");
  });
});

describe("formatSpeed / formatEta / formatPercent", () => {
  it("formats speeds", () => {
    expect(formatSpeed(0)).toBe("–");
    expect(formatSpeed(52_428_800)).toBe("50,0 MB/s");
  });

  it("formats ETAs", () => {
    expect(formatEta(null)).toBe("–");
    expect(formatEta(30)).toBe("< 1 Min.");
    expect(formatEta(185)).toBe("~3 Min.");
    expect(formatEta(3600)).toBe("~1 Std.");
    expect(formatEta(4800)).toBe("~1 Std. 20 Min.");
  });

  it("formats percentages without exceeding 100", () => {
    expect(formatPercent(0, 0)).toBe("0 %");
    expect(formatPercent(50, 200)).toBe("25 %");
    expect(formatPercent(300, 200)).toBe("100 %");
  });
});

describe("formatRelativeDay / truncate", () => {
  it("labels today and yesterday", () => {
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86_400_000);
    expect(formatRelativeDay(today.toISOString())).toBe("Heute");
    expect(formatRelativeDay(yesterday.toISOString())).toBe("Gestern");
    expect(formatRelativeDay("nonsense")).toBe("");
  });

  it("truncates with an ellipsis", () => {
    expect(truncate("kurz", 10)).toBe("kurz");
    expect(truncate("ein sehr langer Titel", 9)).toBe("ein sehr…");
    expect(truncate("ein sehr langer Titel", 9)).toHaveLength(9);
  });
});
