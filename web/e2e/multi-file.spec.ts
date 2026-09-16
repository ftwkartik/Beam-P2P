import { createHash } from "node:crypto";
import { basename, join } from "node:path";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";

import { expect, test } from "@playwright/test";

import { createRoom, hashDownloadLink, joinRoom, waitForConnected } from "./fixtures";

/** A small folder with a nested subdirectory, so the manifest's relative-path
 * handling (buildManifest()'s `file.webkitRelativePath`, docs/security.md §4's path
 * safety) is exercised against real browser-supplied paths, not synthetic ones. */
function makeFolder(): { dir: string; files: { relPath: string; sha256: string }[] } {
  const dir = mkdtempSync(join(tmpdir(), "beam-e2e-folder-"));
  mkdirSync(join(dir, "nested"));

  const entries = [
    { rel: "top.txt", content: "top level file\n" },
    { rel: join("nested", "inner.txt"), content: "nested file content\n" },
  ];
  // A browser's webkitdirectory picker reports each file's path prefixed with the
  // picked directory's own name (e.g. "beam-e2e-folder-xyz/nested/inner.txt"), not
  // just the path relative to it -- match what buildManifest() actually receives.
  const folderName = basename(dir);
  const files = entries.map(({ rel, content }) => {
    writeFileSync(join(dir, rel), content);
    return {
      relPath: `${folderName}/${rel.split("\\").join("/")}`,
      sha256: createHash("sha256").update(content).digest("hex"),
    };
  });
  return { dir, files };
}

test("scenario 2: a folder with a nested path transfers every file intact", async ({ browser }) => {
  const sender = await browser.newPage();
  const receiver = await browser.newPage();

  const code = await createRoom(sender);
  await joinRoom(receiver, code);
  await Promise.all([waitForConnected(sender), waitForConnected(receiver)]);

  const folder = makeFolder();
  await sender.getByTestId("folder-input").setInputFiles(folder.dir);

  await expect(receiver.getByText(/wants to send you 2 files/)).toBeVisible();
  for (const file of folder.files) {
    await expect(receiver.getByText(file.relPath)).toBeVisible();
  }
  await receiver.getByRole("button", { name: "Accept" }).click();

  await expect(sender.getByText("Done.")).toBeVisible({ timeout: 20_000 });

  for (const file of folder.files) {
    // The visible text is "Save <full relative path> (<size>)" -- match on the
    // basename since the full path also embeds this run's generated temp dir name.
    const name = file.relPath.split("/").pop()!;
    await expect(receiver.getByText(name, { exact: false })).toBeVisible();
    const actualHash = await hashDownloadLink(receiver, new RegExp(`Save .*${name}`));
    expect(actualHash).toBe(file.sha256);
  }
});
