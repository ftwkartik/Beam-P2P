import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { InvalidCodeError, normalizeCode, roomCodeToString, suggestCompletions } from "./codes";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VECTORS_DIR = path.resolve(__dirname, "../../../tests/vectors");

interface CodeVector {
  raw: string;
  nameplate: number;
  words: string[];
  canonical: string;
}

function loadVectors(): CodeVector[] {
  const data = JSON.parse(readFileSync(path.join(VECTORS_DIR, "codes.json"), "utf-8"));
  return data.cases;
}

describe("normalizeCode golden vectors", () => {
  const cases = loadVectors();

  it("has at least one vector", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases)("normalizes %j", (testCase) => {
    const parsed = normalizeCode(testCase.raw);
    expect(parsed.nameplate).toBe(testCase.nameplate);
    expect(parsed.words).toEqual(testCase.words);
    expect(roomCodeToString(parsed)).toBe(testCase.canonical);
  });
});

describe("normalizeCode error cases", () => {
  it("rejects the wrong number of tokens", () => {
    expect(() => normalizeCode("7-otter-lantern")).toThrow(InvalidCodeError);
  });

  it("rejects a missing nameplate", () => {
    expect(() => normalizeCode("otter-lantern-tiger-fox")).toThrow(InvalidCodeError);
  });

  it("rejects an out-of-range nameplate", () => {
    expect(() => normalizeCode("99999-otter-lantern-tiger")).toThrow(InvalidCodeError);
  });

  it("rejects a word not in the wordlist", () => {
    expect(() => normalizeCode("7-otter-lantern-notarealword")).toThrow(InvalidCodeError);
  });
});

describe("suggestCompletions", () => {
  it("returns matches starting with the prefix", () => {
    const matches = suggestCompletions("otte");
    expect(matches).toContain("otter");
    expect(matches.every((w) => w.startsWith("otte"))).toBe(true);
  });

  it("returns nothing for an empty prefix", () => {
    expect(suggestCompletions("")).toEqual([]);
  });

  it("respects the limit", () => {
    expect(suggestCompletions("a", 3)).toHaveLength(3);
  });
});
