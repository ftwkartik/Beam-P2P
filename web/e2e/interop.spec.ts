import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { cliReceive, cliSend, type CliProcess } from "./cli";
import { createRoom, expect, hashDownloadLink, joinRoom, makeRandomFile, test } from "./fixtures";

/** docs/testing-strategy.md's "Interop" row: Milestone 10's CLI must actually
 * interoperate with the browser client over a real WebRTC connection, in both
 * directions, verified by hash -- not just against itself (cli/tests/
 * test_transfer_integration.py already covers CLI<->CLI). Runs a real `beam`
 * subprocess against the same compose stack `happy-path.spec.ts` uses. */
test.describe("CLI <-> browser interop", () => {
  let cli: CliProcess | undefined;

  test.afterEach(() => {
    cli?.kill();
    cli = undefined;
  });

  test("scenario 8: CLI sends, browser receives", async ({ newPage, baseURL }) => {
    const file = makeRandomFile("interop-cli-to-browser.bin", 2 * 1024 * 1024);

    const started = await cliSend(file.path, baseURL!);
    cli = started.cli;

    const receiver = await newPage();
    await joinRoom(receiver, started.code);
    // Not waitForConnected(): the CLI sender offers immediately once connected (no
    // human pause the way a browser sender waits for a file to be picked), so the
    // SAS-only screen can be replaced by the next render before an assertion on its
    // transient text ever catches it. SAS *equality* is already covered elsewhere
    // (protocol/tests' golden vectors, happy-path.spec.ts) -- this test is about the
    // transfer, so it waits on the state that actually matters here instead.
    await expect(receiver.getByText(/wants to send you/)).toBeVisible({ timeout: 30_000 });
    await receiver.getByRole("button", { name: "Accept" }).click();

    const name = basename(file.path);
    await expect(receiver.getByText(`Save ${name}`)).toBeVisible({ timeout: 60_000 });

    const actualHash = await hashDownloadLink(receiver, new RegExp(`Save ${name}`));
    expect(actualHash).toBe(file.sha256);

    expect(await cli.waitForExit()).toBe(0);
  });

  test("scenario 9: browser sends, CLI receives", async ({ newPage, baseURL }) => {
    const file = makeRandomFile("interop-browser-to-cli.bin", 2 * 1024 * 1024);
    const receiveDir = mkdtempSync(join(tmpdir(), "beam-e2e-cli-receive-"));

    const sender = await newPage();
    const code = await createRoom(sender);
    // The CLI has to actually join before the browser (alone in the room so far)
    // can ever become connected -- start it first, then wait.
    cli = cliReceive(code, receiveDir, baseURL!);
    // Not waitForConnected(): the SAS-only panel is genuinely transient (it's gone
    // by the time a real CLI subprocess -- with its own signaling+ICE startup
    // latency, unlike two browser tabs racing through the same event loop --
    // finishes connecting and this assertion gets a chance to poll). "Connected" is
    // the stable status text that persists for the rest of the session.
    await expect(sender.getByText("Connected")).toBeVisible({ timeout: 30_000 });

    await sender.getByTestId("file-input").setInputFiles(file.path);
    await expect(sender.getByText("Done.")).toBeVisible({ timeout: 60_000 });

    expect(await cli.waitForExit()).toBe(0);

    const name = basename(file.path);
    const received = readFileSync(join(receiveDir, name));
    const actualHash = createHash("sha256").update(received).digest("hex");
    expect(actualHash).toBe(file.sha256);
  });
});
