import { describe, expect, it } from "vitest";

import {
  ManifestError,
  PathSafetyError,
  buildManifest,
  validateRelativePath,
} from "./manifest";

function fileWithRelativePath(name: string, relativePath: string, bytes = "x"): File {
  const file = new File([bytes], name, { type: "text/plain" });
  Object.defineProperty(file, "webkitRelativePath", { value: relativePath, configurable: true });
  return file;
}

describe("validateRelativePath", () => {
  const validPaths = ["a.txt", "folder/file.txt", "a/b/c/d/deep-file.bin", "résumé.pdf", "no-extension"];

  it.each(validPaths)("accepts %j", (path) => {
    expect(validateRelativePath(path)).toBe(path);
  });

  const invalidPaths: [string, string][] = [
    ["../x", "parent traversal"],
    ["a/../../b", "parent traversal mid-path"],
    ["/etc/passwd", "absolute path"],
    ["~/secrets", "home-relative path"],
    ["C:\\x", "drive letter with backslash"],
    ["a\\b", "backslash separator"],
    ["a/./b", "current-dir segment"],
    ["a//b", "empty segment"],
    ["", "empty path"],
    [" a.txt", "leading whitespace"],
    ["a.txt ", "trailing whitespace"],
    ["a\x00b", "NUL byte"],
    ["CON", "reserved Windows device name"],
    ["CON.txt", "reserved Windows device name with extension"],
    ["folder/COM1", "reserved name in a nested segment"],
    ["a".repeat(300), "path segment too long"],
  ];

  it.each(invalidPaths)("rejects %j (%s)", (path) => {
    expect(() => validateRelativePath(path)).toThrow(PathSafetyError);
  });

  it("rejects a path exceeding the total byte limit", () => {
    // 60 segments * 80 bytes + 59 separators = 4859 bytes, over the 4096 total limit,
    // while each segment (80 bytes) stays well under the per-segment limit on its own.
    const longPath = Array.from({ length: 60 }, () => "a".repeat(80)).join("/");
    expect(() => validateRelativePath(longPath)).toThrow(PathSafetyError);
  });
});

describe("buildManifest", () => {
  it("builds offers for a flat file list", () => {
    const files = [new File(["hello"], "a.txt"), new File(["world"], "b.txt")];
    const offers = buildManifest(files);

    expect(offers).toHaveLength(2);
    expect(offers[0]).toMatchObject({ index: 0, path: "a.txt", size: 5 });
    expect(offers[1]).toMatchObject({ index: 1, path: "b.txt", size: 5 });
  });

  it("prefers webkitRelativePath for directory selections", () => {
    const files = [fileWithRelativePath("file.txt", "my-folder/nested/file.txt")];
    const offers = buildManifest(files);
    expect(offers[0].path).toBe("my-folder/nested/file.txt");
  });

  it("defaults mime to application/octet-stream when the browser reports none", () => {
    const file = new File(["x"], "data.bin");
    const [offer] = buildManifest([file]);
    expect(offer.mime).toBe("application/octet-stream");
  });

  it("rejects an empty file list", () => {
    expect(() => buildManifest([])).toThrow(ManifestError);
  });

  it("rejects duplicate resulting paths", () => {
    const files = [fileWithRelativePath("a.txt", "dir/file.txt"), fileWithRelativePath("b.txt", "dir/file.txt")];
    expect(() => buildManifest(files)).toThrow(ManifestError);
  });

  it("rejects a file whose path fails safety validation", () => {
    const files = [fileWithRelativePath("evil", "../escape.txt")];
    expect(() => buildManifest(files)).toThrow(PathSafetyError);
  });

  it("assigns sequential unique indices", () => {
    const files = [new File(["a"], "a"), new File(["b"], "b"), new File(["c"], "c")];
    const offers = buildManifest(files);
    expect(offers.map((f) => f.index)).toEqual([0, 1, 2]);
  });
});
