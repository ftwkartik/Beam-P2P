import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration } from "./format";

describe("formatBytes", () => {
  it("formats bytes below 1 KB plainly", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
  });

  it("formats KB/MB/GB with one decimal under 10 units", () => {
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1.0 GB");
  });

  it("drops the decimal at 10 or more units", () => {
    expect(formatBytes(12 * 1024)).toBe("12 KB");
  });
});

describe("formatDuration", () => {
  it("handles sub-second and non-finite input", () => {
    expect(formatDuration(0.4)).toBe("<1s");
    expect(formatDuration(Infinity)).toBe("--");
    expect(formatDuration(NaN)).toBe("--");
    expect(formatDuration(-1)).toBe("--");
  });

  it("formats seconds and minutes", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(90)).toBe("1m 30s");
  });

  it("formats hours", () => {
    expect(formatDuration(3661)).toBe("1h 1m");
  });
});
