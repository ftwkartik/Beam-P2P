import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "./session";
import { BeamSession } from "./session";
import type { PeerConnectionOptions } from "./peer";
import type { SignalingClientOptions } from "./signaling";

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

class FakePeerConnection {
  options: PeerConnectionOptions;
  closeSpy = vi.fn();
  handleSignalSpy = vi.fn();
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
