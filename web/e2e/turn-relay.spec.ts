import { createRoom, expect, hashDownloadLink, joinRoom, makeRandomFile, test, waitForConnected } from "./fixtures";

test("scenario 7: a transfer completes when forced through coturn as a relay", async ({ newPage }) => {
  const sender = await newPage();
  const receiver = await newPage();

  // ?forceRelay=1 sets iceTransportPolicy: "relay" (session.ts), so host and srflx
  // candidates are never even offered -- the connection can only succeed via
  // coturn's allocated relay candidates. docs/adr/004's credential-minting and
  // coturn-rejects-bad-credentials paths are already covered at the integration
  // level (server/tests); this is the "a real transfer actually completes over the
  // relay" proof the roadmap asks for.
  const code = await createRoom(sender, "forceRelay=1");
  await joinRoom(receiver, code, "forceRelay=1");
  await Promise.all([waitForConnected(sender), waitForConnected(receiver)]);

  const file = makeRandomFile("turn-relay.bin", 2 * 1024 * 1024, 0x4444);
  await sender.getByTestId("file-input").setInputFiles(file.path);

  await expect(receiver.getByText(/wants to send you/)).toBeVisible();
  await receiver.getByRole("button", { name: "Accept" }).click();

  await expect(receiver.getByText("Save turn-relay.bin")).toBeVisible({ timeout: 30_000 });
  await expect(sender.getByText("Done.")).toBeVisible({ timeout: 30_000 });

  const actualHash = await hashDownloadLink(receiver, /Save turn-relay\.bin/);
  expect(actualHash).toBe(file.sha256);
});
