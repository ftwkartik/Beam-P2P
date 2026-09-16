import { createHash } from "node:crypto";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

/** Creates a room from a fresh HomePage and returns its share code. */
export async function createRoom(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Create a room" }).click();
  await expect(page.getByText("Share this with the other device")).toBeVisible({ timeout: 15_000 });
  const code = (await page.locator("p.font-mono").first().textContent())?.trim();
  if (!code) throw new Error("room code did not render");
  return code;
}

/** Joins a room via the share-link route (`/r#<code>`), the same path a real shared
 * link takes -- not the HomePage's manual code-entry form. */
export async function joinRoom(page: Page, code: string): Promise<void> {
  await page.goto(`/r#${code}`);
  await page.getByRole("button", { name: "Join room" }).click();
}

export async function waitForConnected(page: Page, timeout = 30_000): Promise<void> {
  await expect(page.getByText("Read these symbols aloud")).toBeVisible({ timeout });
}

export async function getSas(page: Page): Promise<string> {
  const text = await page.locator("p.text-2xl").textContent();
  if (!text) throw new Error("SAS did not render");
  return text.trim();
}

/** A temp directory this run owns, for generated fixture files. Playwright's
 * `setInputFiles` needs real paths on disk -- it drives a native file input. */
const scratchDir = mkdtempSync(join(tmpdir(), "beam-e2e-"));

/** Writes a `sizeBytes`-long file of deterministic pseudo-random content (an xorshift
 * PRNG, not `Math.random()`/`crypto.randomBytes`, so a failing test's seed is
 * reproducible) and returns its path and SHA-256 hex digest. */
export function makeRandomFile(name: string, sizeBytes: number, seed = 0x1234): { path: string; sha256: string } {
  const bytes = Buffer.alloc(sizeBytes);
  let state = seed;
  for (let i = 0; i < sizeBytes; i++) {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    bytes[i] = state & 0xff;
  }
  const path = join(scratchDir, name);
  writeFileSync(path, bytes);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  return { path, sha256 };
}

/** Fetches the object URL behind a "Save <name>" link and hashes its bytes in-page
 * (Web Crypto), rather than driving a real filesystem download -- the app never
 * writes to disk itself until the user clicks Save, and this is what the link
 * actually points at (`URL.createObjectURL`, see components/TransferPanel.tsx). */
export async function hashDownloadLink(page: Page, linkText: RegExp | string): Promise<string> {
  const href = await page.getByRole("link", { name: linkText }).getAttribute("href");
  if (!href) throw new Error(`no download link matching ${String(linkText)}`);
  return page.evaluate(async (url) => {
    const res = await fetch(url);
    const buf = await res.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }, href);
}
