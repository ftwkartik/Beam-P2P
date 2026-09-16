import { expect, test } from "@playwright/test";

import { createRoom, getSas, hashDownloadLink, joinRoom, makeRandomFile, waitForConnected } from "./fixtures";

test.describe("full transfer, sender to receiver", () => {
  test("scenario 1: a multi-megabyte file transfers intact and both sides agree on the SAS", async ({
    browser,
  }) => {
    const sender = await browser.newPage();
    const receiver = await browser.newPage();

    const code = await createRoom(sender);
    await joinRoom(receiver, code);
    await Promise.all([waitForConnected(sender), waitForConnected(receiver)]);

    const [sasSender, sasReceiver] = await Promise.all([getSas(sender), getSas(receiver)]);
    expect(sasSender).toBe(sasReceiver);

    const file = makeRandomFile("scenario1.bin", 20 * 1024 * 1024);
    await sender.getByTestId("file-input").setInputFiles(file.path);

    await expect(receiver.getByText(/wants to send you/)).toBeVisible();
    await receiver.getByRole("button", { name: "Accept" }).click();

    await expect(receiver.getByText("Save scenario1.bin")).toBeVisible({ timeout: 60_000 });
    await expect(sender.getByText("Done.")).toBeVisible({ timeout: 60_000 });

    const actualHash = await hashDownloadLink(receiver, /Save scenario1\.bin/);
    expect(actualHash).toBe(file.sha256);
  });
});
