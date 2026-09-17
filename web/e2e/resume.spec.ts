import { createRoom, expect, hashDownloadLink, joinRoom, makeRandomFile, test, waitForConnected } from "./fixtures";

test.describe("resume", () => {
  test("scenario 4: a network drop mid-transfer recovers and completes", async ({ newPage, newPeerContext }) => {
    test.setTimeout(120_000);
    const sender = await newPage();
    const receiverContext = await newPeerContext();
    const receiver = await receiverContext.newPage();

    const code = await createRoom(sender);
    await joinRoom(receiver, code);
    await Promise.all([waitForConnected(sender), waitForConnected(receiver)]);

    const file = makeRandomFile("resume-drop.bin", 40 * 1024 * 1024, 0x2222);
    await sender.getByTestId("file-input").setInputFiles(file.path);
    await receiver.getByRole("button", { name: "Accept" }).click();

    // Let a real chunk of the file actually move before pulling the plug -- both the
    // WS signaling connection and any in-flight WebRTC traffic drop with it.
    await expect(receiver.getByText(/\d+(\.\d+)? (KB|MB) \/ 40(\.0)? MB/)).toBeVisible({ timeout: 20_000 });

    await receiverContext.setOffline(true);
    await new Promise((resolve) => setTimeout(resolve, 2000));
    await receiverContext.setOffline(false);

    await expect(receiver.getByText("Save resume-drop.bin")).toBeVisible({ timeout: 60_000 });
    await expect(sender.getByText("Done.")).toBeVisible({ timeout: 60_000 });

    const actualHash = await hashDownloadLink(receiver, /Save resume-drop\.bin/);
    expect(actualHash).toBe(file.sha256);
  });

  test("scenario 5: a receiver reload mid-transfer resumes from the persisted bitmap", async ({ newPage }) => {
    test.setTimeout(120_000);
    const sender = await newPage();
    const receiver = await newPage();

    const code = await createRoom(sender);
    await joinRoom(receiver, code);
    await Promise.all([waitForConnected(sender), waitForConnected(receiver)]);

    const file = makeRandomFile("resume-reload.bin", 40 * 1024 * 1024, 0x3333);
    await sender.getByTestId("file-input").setInputFiles(file.path);
    await receiver.getByRole("button", { name: "Accept" }).click();

    await expect(receiver.getByText(/\d+(\.\d+)? (KB|MB) \/ 40(\.0)? MB/)).toBeVisible({ timeout: 20_000 });

    // A full page reload: every in-memory object (session, receiver, OPFS worker
    // handle) is gone. Only sessionStorage (the room token) and OPFS's on-disk bytes
    // survive it -- see room-persistence.ts and storage/opfs-storage.ts.
    await receiver.reload();

    // A post-reload renegotiation redoes ICE gathering from scratch and can take
    // noticeably longer than the original connection.
    await waitForConnected(receiver, 40_000);
    await expect(receiver.getByText("Save resume-reload.bin")).toBeVisible({ timeout: 60_000 });
    await expect(sender.getByText("Done.")).toBeVisible({ timeout: 60_000 });

    const actualHash = await hashDownloadLink(receiver, /Save resume-reload\.bin/);
    expect(actualHash).toBe(file.sha256);
  });
});
