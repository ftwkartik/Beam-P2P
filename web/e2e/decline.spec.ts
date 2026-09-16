import { expect, test } from "@playwright/test";

import { createRoom, joinRoom, makeRandomFile, waitForConnected } from "./fixtures";

test("scenario 3: declining a transfer tells the sender and the receiver never shows a save link", async ({
  browser,
}) => {
  const sender = await browser.newPage();
  const receiver = await browser.newPage();

  const code = await createRoom(sender);
  await joinRoom(receiver, code);
  await Promise.all([waitForConnected(sender), waitForConnected(receiver)]);

  const file = makeRandomFile("declined.bin", 64 * 1024);
  await sender.getByTestId("file-input").setInputFiles(file.path);

  await expect(receiver.getByText(/wants to send you/)).toBeVisible();
  await receiver.getByRole("button", { name: "Decline" }).click();

  await expect(sender.getByText(/Declined/)).toBeVisible({ timeout: 10_000 });
  await expect(receiver.getByText(/wants to send you/)).toHaveCount(0);
  await expect(receiver.getByText(/Save declined\.bin/)).toHaveCount(0);
});
