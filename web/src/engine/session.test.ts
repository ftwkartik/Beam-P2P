import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PeerConnectionOptions } from "./peer";
import { clearRoomSession, loadRoomSession, saveRoomSession } from "./room-persistence";
import type { ApiClient } from "./session";
import { BeamSession } from "./session";
import type { SignalingClientOptions } from "./signaling";
import { createFrame, packFrame } from "./transfer/framing";
import { deriveFileRootHash, hashBytes, hashBytesHex } from "./transfer/hashing";

class FakeSignalingClient {
  options: SignalingClientOptions;
  closeSpy = vi.fn();
  sendSpy = vi.fn();

  constructor(options: SignalingClientOptions) {
    this.options = options;
  }

  connect(): void {}
  close(): void {
    this.closeSpy();
  }
  send(message: unknown): void {
    this.sendSpy(message);
  }
  getStatus(): string {
    return "open";
  }
}

class FakeChannel {
  send = vi.fn();
  private listeners: ((event: MessageEvent) => void)[] = [];

  addEventListener(_type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.push(listener);
  }

  removeEventListener(_type: string, listener: (event: MessageEvent) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener);
  }

  simulateMessage(data: string | ArrayBuffer): void {
    for (const listener of [...this.listeners]) listener({ data } as MessageEvent);
  }
}

class FakePeerConnection {
  options: PeerConnectionOptions;
  closeSpy = vi.fn();
  handleSignalSpy = vi.fn();
  controlChannel = new FakeChannel();
  dataChannel = new FakeChannel();
  fingerprints: { local: string; remote: string } | null = {
    local: "sha-256 AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB",
    remote: "sha-256 CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD:CD",
  };

  constructor(options: PeerConnectionOptions) {
    this.options = options;
  }

  close(): void {
    this.closeSpy();
  }
  async handleSignal(data: unknown): Promise<void> {
    this.handleSignalSpy(data);
  }
  getFingerprints() {
    return this.fingerprints;
  }
}

function buildFakeApi(overrides: Partial<ApiClient> = {}): {
  api: ApiClient;
  signalingInstances: FakeSignalingClient[];
  peerInstances: FakePeerConnection[];
  session: BeamSession;
} {
  const signalingInstances: FakeSignalingClient[] = [];
  const peerInstances: FakePeerConnection[] = [];

  const api: ApiClient = {
    createRoom: vi.fn().mockResolvedValue({
      room_id: "room-1",
      code: "7-otter-lantern-tiger",
      nameplate: 7,
      expires_at: 123,
      token: "room-token",
    }),
    joinRoom: vi.fn().mockResolvedValue({ room_id: "room-1", expires_at: 123, token: "room-token" }),
    getIceServers: vi.fn().mockResolvedValue({ ice_servers: [{ urls: "stun:example.test" }] }),
    signalingWsUrl: vi.fn().mockReturnValue("ws://example.test/ws"),
    ...overrides,
  };

  const session = new BeamSession({
    api,
    signalingFactory: (options) => {
      const instance = new FakeSignalingClient(options);
      signalingInstances.push(instance);
      return instance as unknown as import("./signaling").SignalingClient;
    },
    peerConnectionFactory: (options) => {
      const instance = new FakePeerConnection(options);
      peerInstances.push(instance);
      return instance as unknown as import("./peer").PeerConnection;
    },
  });

  return { api, signalingInstances, peerInstances, session };
}

beforeEach(() => {
  vi.clearAllMocks();
  clearRoomSession();
});

describe("BeamSession.createRoom", () => {
  it("creates the room, fetches ICE servers and opens signaling with the room token", async () => {
    const { api, session, signalingInstances } = buildFakeApi();

    await session.createRoom();

    expect(api.createRoom).toHaveBeenCalledOnce();
    expect(api.getIceServers).toHaveBeenCalledWith("room-1", "room-token");
    expect(signalingInstances).toHaveLength(1);
    expect(signalingInstances[0].options.token).toBe("room-token");
    expect(session.getState().room).toMatchObject({ roomId: "room-1", code: "7-otter-lantern-tiger" });
  });

  it("moves to the failed phase if room creation fails", async () => {
    const { session } = buildFakeApi({
      createRoom: vi.fn().mockRejectedValue(new Error("network down")),
    });

    await session.createRoom();

    expect(session.getState().phase).toBe("failed");
    expect(session.getState().error).toBe("network down");
  });
});

describe("welcome handling", () => {
  it("moves to waiting_for_peer when alone in the room", async () => {
    const { session, signalingInstances } = buildFakeApi();
    await session.createRoom();

    signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "creator",
      polite: false,
      peers: [{ peer_id: "peer-me", role: "creator", state: "online" }],
      expires_at: "2026-01-01T00:00:00Z",
    });

    expect(session.getState().phase).toBe("waiting_for_peer");
  });

  it("moves to connecting and creates a PeerConnection when the other peer is already present", async () => {
    const { session, signalingInstances, peerInstances } = buildFakeApi();
    await session.createRoom();

    signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "joiner",
      polite: true,
      peers: [
        { peer_id: "peer-me", role: "joiner", state: "online" },
        { peer_id: "peer-other", role: "creator", state: "online" },
      ],
      expires_at: "2026-01-01T00:00:00Z",
    });

    expect(session.getState().phase).toBe("connecting");
    expect(session.getState().otherPeer?.peer_id).toBe("peer-other");
    expect(peerInstances).toHaveLength(1);
    expect(peerInstances[0].options.polite).toBe(true);
  });

  it("forces relay-only ICE when ?forceRelay=1 is set (E2E TURN scenario only)", async () => {
    const originalUrl = window.location.href;
    window.history.pushState({}, "", "/?forceRelay=1");
    try {
      const { session, signalingInstances, peerInstances } = buildFakeApi();
      await session.createRoom();
      signalingInstances[0].options.onWelcome?.({
        type: "welcome",
        peer_id: "peer-me",
        room_id: "room-1",
        role: "joiner",
        polite: true,
        peers: [
          { peer_id: "peer-me", role: "joiner", state: "online" },
          { peer_id: "peer-other", role: "creator", state: "online" },
        ],
        expires_at: "2026-01-01T00:00:00Z",
      });

      expect(peerInstances[0].options.iceTransportPolicy).toBe("relay");
    } finally {
      window.history.pushState({}, "", originalUrl);
    }
  });

  it("does not force relay-only ICE without the query param", async () => {
    const { session, signalingInstances, peerInstances } = buildFakeApi();
    await session.createRoom();
    signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "joiner",
      polite: true,
      peers: [
        { peer_id: "peer-me", role: "joiner", state: "online" },
        { peer_id: "peer-other", role: "creator", state: "online" },
      ],
      expires_at: "2026-01-01T00:00:00Z",
    });

    expect(peerInstances[0].options.iceTransportPolicy).toBeUndefined();
  });
});

describe("peer_joined / peer_reconnecting / peer_left", () => {
  async function setupWaiting() {
    const result = buildFakeApi();
    await result.session.createRoom();
    result.signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "creator",
      polite: false,
      peers: [{ peer_id: "peer-me", role: "creator", state: "online" }],
      expires_at: "2026-01-01T00:00:00Z",
    });
    return result;
  }

  it("creates the PeerConnection once the other peer joins", async () => {
    const { session, signalingInstances, peerInstances } = await setupWaiting();

    signalingInstances[0].options.onPeerJoined?.({
      type: "peer_joined",
      peer_id: "peer-other",
      role: "joiner",
    });

    expect(session.getState().phase).toBe("connecting");
    expect(peerInstances).toHaveLength(1);
    expect(peerInstances[0].options.polite).toBe(false);
  });

  it("marks the other peer reconnecting", async () => {
    const { session, signalingInstances } = await setupWaiting();
    signalingInstances[0].options.onPeerJoined?.({ type: "peer_joined", peer_id: "peer-other", role: "joiner" });

    signalingInstances[0].options.onPeerReconnecting?.({ type: "peer_reconnecting", peer_id: "peer-other" });

    expect(session.getState().otherPeer?.state).toBe("reconnecting");
  });

  it("tears down the peer connection and clears state on peer_left", async () => {
    const { session, signalingInstances, peerInstances } = await setupWaiting();
    signalingInstances[0].options.onPeerJoined?.({ type: "peer_joined", peer_id: "peer-other", role: "joiner" });

    signalingInstances[0].options.onPeerLeft?.({ type: "peer_left", peer_id: "peer-other", reason: "left" });

    expect(peerInstances[0].closeSpy).toHaveBeenCalledOnce();
    expect(session.getState().otherPeer).toBeNull();
    expect(session.getState().phase).toBe("peer_left");
  });
});

describe("connection established", () => {
  it("derives the SAS and moves to connected once the RTCPeerConnection reports connected", async () => {
    const { session, signalingInstances, peerInstances } = buildFakeApi();
    await session.createRoom();
    signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "creator",
      polite: false,
      peers: [
        { peer_id: "peer-me", role: "creator", state: "online" },
        { peer_id: "peer-other", role: "joiner", state: "online" },
      ],
      expires_at: "2026-01-01T00:00:00Z",
    });

    peerInstances[0].options.onConnectionStateChange?.("connected");

    await vi.waitFor(() => expect(session.getState().phase).toBe("connected"));
    expect(session.getState().sas).toHaveLength(5);
  });

  it("forwards outgoing signals from the peer connection to the signaling client", async () => {
    const { session, signalingInstances, peerInstances } = buildFakeApi();
    await session.createRoom();
    signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "creator",
      polite: false,
      peers: [
        { peer_id: "peer-me", role: "creator", state: "online" },
        { peer_id: "peer-other", role: "joiner", state: "online" },
      ],
      expires_at: "2026-01-01T00:00:00Z",
    });

    peerInstances[0].options.onSendSignal({ kind: "candidate", candidate: null });

    expect(signalingInstances[0].sendSpy).toHaveBeenCalledWith({
      type: "signal",
      data: { kind: "candidate", candidate: null },
    });
    void session;
  });
});

describe("fatal signaling errors", () => {
  it("tears everything down and reports an error", async () => {
    const { session, signalingInstances, peerInstances } = buildFakeApi();
    await session.createRoom();
    signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "creator",
      polite: false,
      peers: [
        { peer_id: "peer-me", role: "creator", state: "online" },
        { peer_id: "peer-other", role: "joiner", state: "online" },
      ],
      expires_at: "2026-01-01T00:00:00Z",
    });

    signalingInstances[0].options.onFatal?.(4401, "expired token");

    expect(session.getState().phase).toBe("failed");
    expect(session.getState().error).toContain("4401");
    expect(peerInstances[0].closeSpy).toHaveBeenCalledOnce();
  });
});

describe("leave", () => {
  it("closes signaling and the peer connection and resets to idle", async () => {
    const { session, signalingInstances, peerInstances } = buildFakeApi();
    await session.createRoom();
    signalingInstances[0].options.onWelcome?.({
      type: "welcome",
      peer_id: "peer-me",
      room_id: "room-1",
      role: "creator",
      polite: false,
      peers: [
        { peer_id: "peer-me", role: "creator", state: "online" },
        { peer_id: "peer-other", role: "joiner", state: "online" },
      ],
      expires_at: "2026-01-01T00:00:00Z",
    });

    session.leave();

    expect(signalingInstances[0].closeSpy).toHaveBeenCalledOnce();
    expect(peerInstances[0].closeSpy).toHaveBeenCalledOnce();
    expect(session.getState().phase).toBe("idle");
  });
});

async function connectedSession() {
  const result = buildFakeApi();
  await result.session.createRoom();
  result.signalingInstances[0].options.onWelcome?.({
    type: "welcome",
    peer_id: "peer-me",
    room_id: "room-1",
    role: "creator",
    polite: false,
    peers: [
      { peer_id: "peer-me", role: "creator", state: "online" },
      { peer_id: "peer-other", role: "joiner", state: "online" },
    ],
    expires_at: "2026-01-01T00:00:00Z",
  });
  return result;
}

describe("transfer engine wiring", () => {
  it("sendFiles offers the manifest over the peer connection's control channel", async () => {
    const { session, peerInstances } = await connectedSession();

    session.sendFiles([new File(["hello"], "a.txt")]);

    expect(peerInstances[0].controlChannel.send).toHaveBeenCalledWith(
      expect.stringContaining("offer_files"),
    );
  });

  it("surfaces an incoming offer and accepting it replies over the control channel", async () => {
    const { session, peerInstances } = await connectedSession();

    peerInstances[0].controlChannel.simulateMessage(
      JSON.stringify({
        type: "offer_files",
        transfer_id: "t1",
        block_size: 4,
        files: [{ index: 0, path: "a.txt", size: 5, mime: "text/plain", mtime: null }],
      }),
    );

    expect(session.getState().incomingOffer).toMatchObject({ transferId: "t1" });

    session.acceptIncomingTransfer();

    expect(session.getState().incomingOffer).toBeNull();
    expect(peerInstances[0].controlChannel.send).toHaveBeenCalledWith(
      expect.stringContaining('"accept"'),
    );
  });

  it("declining sends a decline message and clears the incoming offer", async () => {
    const { session, peerInstances } = await connectedSession();

    peerInstances[0].controlChannel.simulateMessage(
      JSON.stringify({
        type: "offer_files",
        transfer_id: "t1",
        block_size: 4,
        files: [{ index: 0, path: "a.txt", size: 5, mime: "text/plain", mtime: null }],
      }),
    );

    session.declineIncomingTransfer("no thanks");

    expect(session.getState().incomingOffer).toBeNull();
    expect(peerInstances[0].controlChannel.send).toHaveBeenCalledWith(
      expect.stringContaining("no thanks"),
    );
  });

  it("routes incoming data frames to the receiver and completes a full small transfer", async () => {
    const { session, peerInstances } = await connectedSession();
    const control = peerInstances[0].controlChannel;

    control.simulateMessage(
      JSON.stringify({
        type: "offer_files",
        transfer_id: "t1",
        block_size: 4,
        files: [{ index: 0, path: "a.txt", size: 4, mime: "text/plain", mtime: null }],
      }),
    );
    session.acceptIncomingTransfer();

    const content = new TextEncoder().encode("abcd");
    const hashHex = await hashBytesHex(content);
    control.simulateMessage(JSON.stringify({ type: "block", file: 0, index: 0, sha256: hashHex }));
    const frame = createFrame(0, 0n, content, true);
    peerInstances[0].dataChannel.simulateMessage(packFrame(frame).buffer as ArrayBuffer);
    await new Promise((resolve) => setTimeout(resolve, 0));

    const rootHash = await deriveFileRootHash(4n, [await hashBytes(content)]);
    control.simulateMessage(JSON.stringify({ type: "file_done", file: 0, sha256: rootHash }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(session.getState().readyFiles).toHaveLength(1);
    expect(session.getState().readyFiles[0].fileIndex).toBe(0);
  });
});

describe("room session persistence and resume", () => {
  it("persists the room session once signaling starts", async () => {
    const { session } = buildFakeApi({
      createRoom: vi.fn().mockResolvedValue({
        room_id: "room-1",
        code: "7-otter-lantern-tiger",
        nameplate: 7,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        token: "room-token",
      }),
    });

    await session.createRoom();

    expect(loadRoomSession()).toMatchObject({ roomId: "room-1", token: "room-token", role: "creator" });
  });

  it("leave() clears the persisted session", async () => {
    const { session } = buildFakeApi({
      createRoom: vi.fn().mockResolvedValue({
        room_id: "room-1",
        code: "7-otter-lantern-tiger",
        nameplate: 7,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        token: "room-token",
      }),
    });
    await session.createRoom();
    expect(loadRoomSession()).not.toBeNull();

    session.leave();

    expect(loadRoomSession()).toBeNull();
  });

  it("a fatal signaling close clears the persisted session", async () => {
    const { session, signalingInstances } = buildFakeApi({
      createRoom: vi.fn().mockResolvedValue({
        room_id: "room-1",
        code: "7-otter-lantern-tiger",
        nameplate: 7,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        token: "room-token",
      }),
    });
    await session.createRoom();
    expect(loadRoomSession()).not.toBeNull();

    signalingInstances[0].options.onFatal?.(4404, "room not found");

    expect(session.getState().phase).toBe("failed");
    expect(loadRoomSession()).toBeNull();
  });

  it("resume() does nothing and returns false when there's no persisted session", async () => {
    const { api, session } = buildFakeApi();

    const resumed = await session.resume();

    expect(resumed).toBe(false);
    expect(api.createRoom).not.toHaveBeenCalled();
    expect(api.joinRoom).not.toHaveBeenCalled();
    expect(session.getState().phase).toBe("idle");
  });

  it("resume() reconnects with the persisted token, skipping the REST create/join step", async () => {
    const { api, session, signalingInstances } = buildFakeApi();
    saveRoomSession({
      roomId: "room-1",
      token: "room-token",
      code: "7-otter-lantern-tiger",
      nameplate: 7,
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
      role: "joiner",
    });

    const resumed = await session.resume();

    expect(resumed).toBe(true);
    expect(api.createRoom).not.toHaveBeenCalled();
    expect(api.joinRoom).not.toHaveBeenCalled();
    expect(api.getIceServers).toHaveBeenCalledWith("room-1", "room-token");
    expect(signalingInstances).toHaveLength(1);
    expect(signalingInstances[0].options.token).toBe("room-token");
    expect(session.getState().room).toMatchObject({ roomId: "room-1", role: "joiner" });
  });
});

function offerFilesTransferId(sendMock: { mock: { calls: unknown[][] } }): string | undefined {
  const messages = sendMock.mock.calls.map((call) => JSON.parse(call[0] as string) as { type: string; transfer_id?: string });
  return messages.find((m) => m.type === "offer_files")?.transfer_id;
}

describe("outgoing transfer resume across a reconnect", () => {
  it("restarts an in-progress outgoing transfer with the same transfer id once reconnected", async () => {
    const { session, signalingInstances, peerInstances } = await connectedSession();

    session.sendFiles([new File(["hello"], "a.txt")]);
    const firstTransferId = offerFilesTransferId(peerInstances[0].controlChannel.send);
    expect(firstTransferId).toBeTruthy();

    // Simulate a reconnect: the other peer is announced again on the *same*
    // signaling client, which tears down and rebuilds the peer connection
    // (session.ts's setupPeerConnection()), wiping the old TransferSender.
    signalingInstances[0].options.onPeerJoined?.({ type: "peer_joined", peer_id: "peer-other", role: "joiner" });
    expect(peerInstances).toHaveLength(2);

    // The new peer connection's control channel reopening is what triggers the
    // restart -- not merely the RTCPeerConnection reaching "connected" (which
    // doesn't itself guarantee the negotiated data channels are open yet; a real
    // send() before that throws, caught only by a real browser -- see
    // onControlChannelOpen's comment in session.ts).
    peerInstances[1].options.onControlChannelOpen?.(peerInstances[1].controlChannel as unknown as RTCDataChannel);
    await vi.waitFor(() => expect(offerFilesTransferId(peerInstances[1].controlChannel.send)).toBeTruthy());

    expect(offerFilesTransferId(peerInstances[1].controlChannel.send)).toBe(firstTransferId);
  });

  it("does not restart a transfer that already finished before the reconnect", async () => {
    const { session, signalingInstances, peerInstances } = await connectedSession();

    session.sendFiles([new File(["hello"], "a.txt")]);
    // A sender never waits for `file_verified` to move on (sender.ts), but it does
    // fail immediately on a declined offer -- the simplest terminal phase to reach
    // without also driving a full block-by-block transfer in this test.
    peerInstances[0].controlChannel.simulateMessage(
      JSON.stringify({ type: "decline", transfer_id: offerFilesTransferId(peerInstances[0].controlChannel.send), reason: "no thanks" }),
    );
    expect(session.getState().outgoingTransfer?.phase).toBe("declined");

    signalingInstances[0].options.onPeerJoined?.({ type: "peer_joined", peer_id: "peer-other", role: "joiner" });
    peerInstances[1].options.onControlChannelOpen?.(peerInstances[1].controlChannel as unknown as RTCDataChannel);
    // Give any (incorrect) restart a chance to happen before asserting it didn't.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(offerFilesTransferId(peerInstances[1].controlChannel.send)).toBeUndefined();
  });
});
