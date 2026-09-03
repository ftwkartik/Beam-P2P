import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { InvalidFingerprintError, deriveSas, formatSas, normalizeFingerprint } from "./sas";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VECTORS_DIR = path.resolve(__dirname, "../../../tests/vectors");

interface SasVector {
  room_id: string;
  fp_a: string;
  fp_b: string;
  symbols: { emoji: string; name: string }[];
}

function loadVectors(): SasVector[] {
  const data = JSON.parse(readFileSync(path.join(VECTORS_DIR, "sas.json"), "utf-8"));
  return data.cases;
}

describe("deriveSas golden vectors", () => {
  const cases = loadVectors();

  it("has at least one vector", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases)("derives the pinned SAS for $room_id", async (testCase) => {
    const symbols = await deriveSas(testCase.room_id, testCase.fp_a, testCase.fp_b);
    expect(symbols).toEqual(testCase.symbols);
  });

  it.each(cases)("is order-independent in the fingerprints for $room_id", async (testCase) => {
    const swapped = await deriveSas(testCase.room_id, testCase.fp_b, testCase.fp_a);
    expect(swapped).toEqual(testCase.symbols);
  });
});

describe("normalizeFingerprint", () => {
  it("uppercases the hex part but not the scheme", () => {
    const fp = "sha-256 ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab:ab";
    const expected =
      "sha-256 AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB";
    expect(normalizeFingerprint(fp)).toBe(expected);
  });

  it("rejects a malformed fingerprint", () => {
    expect(() => normalizeFingerprint("not-a-fingerprint")).toThrow(InvalidFingerprintError);
  });
});

describe("formatSas", () => {
  it("joins symbols with two spaces", () => {
    expect(formatSas([{ emoji: "🐶", name: "dog" }, { emoji: "🐱", name: "cat" }])).toBe(
      "🐶 dog  🐱 cat",
    );
  });
});
