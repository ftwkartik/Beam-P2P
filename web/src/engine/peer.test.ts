import { describe, expect, it, vi } from "vitest";

import { PeerConnection } from "./peer";

/** A minimal fake RTCDataChannel: enough surface for PeerConnection to drive it. */
class FakeDataChannel {
  label: string;
  listeners: Record<string, (() => void)[]> = {};

  constructor(label: string) {
    this.label = label;
  }

  addEventListener(type: string, handler: () => void): void {
    (this.listeners[type] ??= []).push(handler);
  }

  emitOpen(): void {
    for (const handler of this.listeners.open ?? []) handler();
  }

  close(): void {}
}

/** A minimal fake RTCPeerConnection implementing only what PeerConnection touches,
 * with test hooks (`emit*`) to drive the perfect-negotiation state machine directly --
 * jsdom has no real WebRTC implementation to test against. */
class FakeRTCPeerConnection {
  signalingState: RTCSignalingState = "stable";
  iceConnectionState: RTCIceConnectionState = "new";
  connectionState: RTCPeerConnectionState = "new";
  localDescription: RTCSessionDescription | null = null;
  remoteDescription: RTCSessionDescription | null = null;

  restartIceCalls = 0;
  setLocalDescriptionCalls: (RTCLocalSessionDescriptionInit | undefined)[] = [];
  setRemoteDescriptionCalls: RTCSessionDescriptionInit[] = [];
  addIceCandidateCalls: RTCIceCandidateInit[] = [];
  channels: FakeDataChannel[] = [];

  private listeners: Record<string, (() => void)[]> = {};
  private failSetRemoteDescription = false;
  private failAddIceCandidate = false;

  createDataChannel(label: string): FakeDataChannel {
    const channel = new FakeDataChannel(label);
    this.channels.push(channel);
    return channel;
  }

  addEventListener(type: string, handler: () => void): void {
    (this.listeners[type] ??= []).push(handler);
  }

  async setLocalDescription(description?: RTCLocalSessionDescriptionInit): Promise<void> {
    this.setLocalDescriptionCalls.push(description);
    const type = description?.type ?? (this.signalingState === "have-remote-offer" ? "answer" : "offer");
    this.signalingState = type === "offer" ? "have-local-offer" : "stable";
    this.localDescription = { type, sdp: fakeSdp() } as RTCSessionDescription;
  }

  async setRemoteDescription(description: RTCSessionDescriptionInit): Promise<void> {
    if (this.failSetRemoteDescription) throw new Error("setRemoteDescription failed");
    this.setRemoteDescriptionCalls.push(description);
    this.signalingState = description.type === "offer" ? "have-remote-offer" : "stable";
    this.remoteDescription = { type: description.type, sdp: description.sdp } as RTCSessionDescription;
  }

  async addIceCandidate(candidate: RTCIceCandidateInit): Promise<void> {
    if (this.failAddIceCandidate) throw new Error("addIceCandidate failed");
    this.addIceCandidateCalls.push(candidate);
  }

  restartIce(): void {
    this.restartIceCalls++;
  }

  close(): void {}

  // --- test hooks --------------------------------------------------------------
  emitNegotiationNeeded(): void {
    for (const h of this.listeners.negotiationneeded ?? []) h();
  }

  emitIceConnectionStateChange(state: RTCIceConnectionState): void {
    this.iceConnectionState = state;
    for (const h of this.listeners.iceconnectionstatechange ?? []) h();
  }

  emitConnectionStateChange(state: RTCPeerConnectionState): void {
    this.connectionState = state;
    for (const h of this.listeners.connectionstatechange ?? []) h();
  }

  simulateFailedSetRemoteDescription(): void {
    this.failSetRemoteDescription = true;
  }

  simulateFailedAddIceCandidate(): void {
    this.failAddIceCandidate = true;
  }
}

function fakeSdp(): string {
  return "v=0\r\na=fingerprint:sha-256 AB:CD:EF:01:23:45:67:89\r\n";
}

function factory(): { pc: FakeRTCPeerConnection; peer: PeerConnection } {
  const pc = new FakeRTCPeerConnection();
  const peer = new PeerConnection({
    polite: true,
    iceServers: [],
    onSendSignal: () => {},
    rtcFactory: () => pc as unknown as RTCPeerConnection,
  });
  return { pc, peer };
}

describe("PeerConnection", () => {
  it("creates the control and data channels with the fixed negotiated IDs", () => {
    const pc = new FakeRTCPeerConnection();
    const createSpy = vi.spyOn(pc, "createDataChannel");
    new PeerConnection({
      polite: false,
      iceServers: [],
      onSendSignal: () => {},
      rtcFactory: () => pc as unknown as RTCPeerConnection,
    });

    expect(createSpy).toHaveBeenNthCalledWith(1, "control", {
      negotiated: true,
      id: 0,
      ordered: true,
    });
    expect(createSpy).toHaveBeenNthCalledWith(2, "data", {
      negotiated: true,
      id: 1,
      ordered: false,
    });
  });

  it("sends its local description as an offer on negotiationneeded", async () => {
    const onSendSignal = vi.fn();
    const pc = new FakeRTCPeerConnection();
    new PeerConnection({
      polite: false,
      iceServers: [],
      onSendSignal,
      rtcFactory: () => pc as unknown as RTCPeerConnection,
    });

    pc.emitNegotiationNeeded();
    await vi.waitFor(() => expect(onSendSignal).toHaveBeenCalled());

    expect(onSendSignal).toHaveBeenCalledWith({
      kind: "description",
      description: { type: "offer", sdp: expect.stringContaining("fingerprint") },
    });
  });

  it("answers an incoming offer", async () => {
    const { pc, peer } = factory();

    await peer.handleSignal({ kind: "description", description: { type: "offer", sdp: fakeSdp() } });

    expect(pc.setRemoteDescriptionCalls).toHaveLength(1);
    expect(pc.setLocalDescriptionCalls).toHaveLength(1);
    expect(pc.localDescription?.type).toBe("answer");
  });

  it("adds a non-null ICE candidate once a remote description is set, and ignores the null end-of-candidates marker", async () => {
    const { pc, peer } = factory();
    await peer.handleSignal({ kind: "description", description: { type: "offer", sdp: fakeSdp() } });

    await peer.handleSignal({
      kind: "candidate",
      candidate: { candidate: "candidate:1 1 UDP 1 1.2.3.4 1234 typ host", sdpMid: "0", sdpMLineIndex: 0 },
    });
    expect(pc.addIceCandidateCalls).toHaveLength(1);

    await peer.handleSignal({ kind: "candidate", candidate: null });
    expect(pc.addIceCandidateCalls).toHaveLength(1); // unchanged
  });

  it("buffers a candidate that arrives before the remote description and flushes it after", async () => {
    const { pc, peer } = factory();

    await peer.handleSignal({
      kind: "candidate",
      candidate: { candidate: "candidate:1 1 UDP 1 1.2.3.4 1234 typ host" },
    });
    expect(pc.addIceCandidateCalls).toHaveLength(0); // no remote description yet -- buffered

    await peer.handleSignal({ kind: "description", description: { type: "offer", sdp: fakeSdp() } });
    expect(pc.addIceCandidateCalls).toHaveLength(1); // flushed once the remote description landed
  });

  describe("perfect negotiation collision handling", () => {
    it("the polite peer rolls back and accepts a colliding offer", async () => {
      const { pc, peer } = factory(); // polite: true

      // Simulate "makingOffer" mid-flight by triggering our own negotiationneeded
      // first without awaiting, then delivering the remote's offer concurrently.
      pc.emitNegotiationNeeded();

      await peer.handleSignal({
        kind: "description",
        description: { type: "offer", sdp: fakeSdp() },
      });

      // Polite peers never set ignoreOffer, so the remote offer is always applied.
      expect(pc.setRemoteDescriptionCalls).toHaveLength(1);
      expect(pc.setRemoteDescriptionCalls[0].type).toBe("offer");
    });

    it("the impolite peer ignores a colliding offer while it has an offer in flight", async () => {
      const pc = new FakeRTCPeerConnection();
      const peer = new PeerConnection({
        polite: false,
        iceServers: [],
        onSendSignal: () => {},
        rtcFactory: () => pc as unknown as RTCPeerConnection,
      });

      // Put the connection in have-local-offer, as if our own offer is in flight.
      pc.signalingState = "have-local-offer";

      await peer.handleSignal({
        kind: "description",
        description: { type: "offer", sdp: fakeSdp() },
      });

      expect(pc.setRemoteDescriptionCalls).toHaveLength(0);
    });

    it("the impolite peer still accepts an offer when it is not itself offering", async () => {
      const pc = new FakeRTCPeerConnection();
      const peer = new PeerConnection({
        polite: false,
        iceServers: [],
        onSendSignal: () => {},
        rtcFactory: () => pc as unknown as RTCPeerConnection,
      });

      await peer.handleSignal({
        kind: "description",
        description: { type: "offer", sdp: fakeSdp() },
      });

      expect(pc.setRemoteDescriptionCalls).toHaveLength(1);
    });

    it("swallows an addIceCandidate failure for a candidate tied to an ignored renegotiation offer", async () => {
      const pc = new FakeRTCPeerConnection();
      const peer = new PeerConnection({
        polite: false,
        iceServers: [],
        onSendSignal: () => {},
        rtcFactory: () => pc as unknown as RTCPeerConnection,
      });
      // An initial, accepted offer -- remoteDescription is now set, so a later
      // candidate won't be buffered waiting for one.
      await peer.handleSignal({
        kind: "description",
        description: { type: "offer", sdp: fakeSdp() },
      });
      // A renegotiation collision: we're mid-offer ourselves, so the impolite peer
      // ignores this second incoming offer.
      pc.signalingState = "have-local-offer";
      await peer.handleSignal({
        kind: "description",
        description: { type: "offer", sdp: fakeSdp() },
      });
      pc.simulateFailedAddIceCandidate();

      await expect(
        peer.handleSignal({
          kind: "candidate",
          candidate: { candidate: "candidate:1 1 UDP 1 1.2.3.4 1234 typ host" },
        }),
      ).resolves.toBeUndefined();
    });
  });

  it("restarts ICE when the connection state goes to failed", () => {
    const { pc } = factory();
    pc.emitIceConnectionStateChange("failed");
    expect(pc.restartIceCalls).toBe(1);
  });

  it("reports connection state changes", () => {
    const onConnectionStateChange = vi.fn();
    const pc = new FakeRTCPeerConnection();
    new PeerConnection({
      polite: true,
      iceServers: [],
      onSendSignal: () => {},
      onConnectionStateChange,
      rtcFactory: () => pc as unknown as RTCPeerConnection,
    });

    pc.emitConnectionStateChange("connected");
    expect(onConnectionStateChange).toHaveBeenCalledWith("connected");
  });

  it("fires onControlChannelOpen and onDataChannelOpen", () => {
    const onControlChannelOpen = vi.fn();
    const onDataChannelOpen = vi.fn();
    const pc = new FakeRTCPeerConnection();
    new PeerConnection({
      polite: true,
      iceServers: [],
      onSendSignal: () => {},
      onControlChannelOpen,
      onDataChannelOpen,
      rtcFactory: () => pc as unknown as RTCPeerConnection,
    });

    pc.channels[0].emitOpen();
    pc.channels[1].emitOpen();
    expect(onControlChannelOpen).toHaveBeenCalledOnce();
    expect(onDataChannelOpen).toHaveBeenCalledOnce();
  });

  describe("getFingerprints", () => {
    it("returns null before both descriptions are set", () => {
      const { peer } = factory();
      expect(peer.getFingerprints()).toBeNull();
    });

    it("extracts both fingerprints once local and remote descriptions are set", async () => {
      const { peer } = factory();
      await peer.handleSignal({
        kind: "description",
        description: { type: "offer", sdp: fakeSdp() },
      });

      const fingerprints = peer.getFingerprints();
      expect(fingerprints).not.toBeNull();
      expect(fingerprints?.local).toMatch(/^sha-256 /);
      expect(fingerprints?.remote).toMatch(/^sha-256 /);
    });
  });
});
