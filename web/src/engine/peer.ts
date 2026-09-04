/**
 * WebRTC peer connection wrapper implementing "perfect negotiation" (docs/protocol.md
 * §4; docs/reference-analysis.md's critique of getting this wrong) plus ICE restart on
 * failure (docs/architecture.md §4.3). Framework-free and independently testable via
 * an injectable `rtcFactory` (docs/adr/007-react-typescript-client.md) -- jsdom (used
 * by Vitest) has no RTCPeerConnection implementation at all.
 *
 * `polite` comes from the server's `welcome` message (the joiner is polite; docs/
 * protocol.md §3), which is what makes offer/answer collisions resolvable without the
 * two peers needing to coordinate a tiebreak themselves.
 */

import type { IceSignal, SdpSignal } from "../protocol/generated/server-message";

/** Fixed IDs so both sides declare the same negotiated channels (docs/protocol.md §4). */
const CONTROL_CHANNEL_ID = 0;
const DATA_CHANNEL_ID = 1;

const FINGERPRINT_RE = /^a=fingerprint:(sha-256 [0-9A-Fa-f:]+)\r?$/m;

export type PeerSignal = SdpSignal | IceSignal;

export interface PeerConnectionOptions {
  polite: boolean;
  iceServers: RTCIceServer[];
  onSendSignal: (data: PeerSignal) => void;
  onConnectionStateChange?: (state: RTCPeerConnectionState) => void;
  onControlChannelOpen?: (channel: RTCDataChannel) => void;
  onDataChannelOpen?: (channel: RTCDataChannel) => void;
  /** Constructs the connection; overridable in tests (jsdom has no RTCPeerConnection). */
  rtcFactory?: (config: RTCConfiguration) => RTCPeerConnection;
}

export interface PeerFingerprints {
  local: string;
  remote: string;
}

function extractFingerprint(sdp: string): string | null {
  const match = FINGERPRINT_RE.exec(sdp);
  return match ? match[1] : null;
}

export class PeerConnection {
  private readonly pc: RTCPeerConnection;
  private readonly options: PeerConnectionOptions;
  private makingOffer = false;
  private ignoreOffer = false;
  private pendingCandidates: RTCIceCandidateInit[] = [];

  readonly controlChannel: RTCDataChannel;
  readonly dataChannel: RTCDataChannel;

  constructor(options: PeerConnectionOptions) {
    this.options = options;
    const factory = options.rtcFactory ?? ((config: RTCConfiguration) => new RTCPeerConnection(config));
    this.pc = factory({ iceServers: options.iceServers });

    this.controlChannel = this.pc.createDataChannel("control", {
      negotiated: true,
      id: CONTROL_CHANNEL_ID,
      ordered: true,
    });
    this.dataChannel = this.pc.createDataChannel("data", {
      negotiated: true,
      id: DATA_CHANNEL_ID,
      ordered: false,
    });

    this.controlChannel.addEventListener("open", () => {
      this.options.onControlChannelOpen?.(this.controlChannel);
    });
    this.dataChannel.addEventListener("open", () => {
      this.options.onDataChannelOpen?.(this.dataChannel);
    });

    this.pc.addEventListener("negotiationneeded", () => {
      void this.handleNegotiationNeeded();
    });
    this.pc.addEventListener("icecandidate", (event) => {
      const candidate = (event as RTCPeerConnectionIceEvent).candidate;
      this.options.onSendSignal({
        kind: "candidate",
        candidate: candidate ? (candidate.toJSON() as IceSignal["candidate"]) : null,
      });
    });
    this.pc.addEventListener("iceconnectionstatechange", () => {
      if (this.pc.iceConnectionState === "failed") {
        this.pc.restartIce();
      }
    });
    this.pc.addEventListener("connectionstatechange", () => {
      this.options.onConnectionStateChange?.(this.pc.connectionState);
    });
  }

  private async handleNegotiationNeeded(): Promise<void> {
    try {
      this.makingOffer = true;
      await this.pc.setLocalDescription();
      this.sendLocalDescription();
    } catch (err) {
      console.error("beam: negotiationneeded failed", err);
    } finally {
      this.makingOffer = false;
    }
  }

  private sendLocalDescription(): void {
    const description = this.pc.localDescription;
    if (!description) return;
    this.options.onSendSignal({
      kind: "description",
      description: { type: description.type, sdp: description.sdp },
    });
  }

  /** Feeds in a `signal` message relayed from the other peer via the signaling server.
   * Discriminated structurally on `"description" in data` (required on SdpSignal,
   * absent on IceSignal) rather than by `data.kind`: the generated types mark `kind`
   * optional (it carries a default server-side, so it's always present on the wire,
   * but that's not visible to json-schema-to-typescript) and `IceSignal.candidate` is
   * also optional, so neither alone lets TypeScript narrow the union reliably.
   *
   * A candidate that arrives before the matching remote description is buffered and
   * flushed once that description is set, rather than dropped or thrown -- verified
   * against a real (non-browser) WebRTC stack in Milestone 7's manual two-peer check,
   * candidates from a fast local network can be dispatched to the signaling channel
   * before the description event listener has run, since nothing in the spec orders
   * one relative to the other once they're on the wire. */
  async handleSignal(data: PeerSignal): Promise<void> {
    try {
      if ("description" in data) {
        const incoming = data.description;
        const offerCollision =
          incoming.type === "offer" && (this.makingOffer || this.pc.signalingState !== "stable");
        this.ignoreOffer = !this.options.polite && offerCollision;
        if (this.ignoreOffer) return;

        await this.pc.setRemoteDescription(incoming as RTCSessionDescriptionInit);
        await this.flushPendingCandidates();
        if (incoming.type === "offer") {
          await this.pc.setLocalDescription();
          this.sendLocalDescription();
        }
        return;
      }

      if (data.candidate == null) return; // end-of-candidates marker; nothing to add
      const candidate = data.candidate as RTCIceCandidateInit;
      if (this.pc.remoteDescription === null) {
        this.pendingCandidates.push(candidate);
        return;
      }
      await this.addIceCandidate(candidate);
    } catch (err) {
      console.error("beam: signal handling failed", err);
    }
  }

  private async flushPendingCandidates(): Promise<void> {
    const candidates = this.pendingCandidates;
    this.pendingCandidates = [];
    for (const candidate of candidates) {
      await this.addIceCandidate(candidate);
    }
  }

  private async addIceCandidate(candidate: RTCIceCandidateInit): Promise<void> {
    try {
      await this.pc.addIceCandidate(candidate);
    } catch (err) {
      if (!this.ignoreOffer) throw err;
    }
  }

  /**
   * Local and remote DTLS fingerprints once both descriptions are set, for SAS
   * derivation (docs/protocol.md §4.4). Returns null before negotiation completes.
   */
  getFingerprints(): PeerFingerprints | null {
    const localSdp = this.pc.localDescription?.sdp;
    const remoteSdp = this.pc.remoteDescription?.sdp;
    if (!localSdp || !remoteSdp) return null;

    const local = extractFingerprint(localSdp);
    const remote = extractFingerprint(remoteSdp);
    if (!local || !remote) return null;

    return { local, remote };
  }

  get connectionState(): RTCPeerConnectionState {
    return this.pc.connectionState;
  }

  close(): void {
    this.controlChannel.close();
    this.dataChannel.close();
    this.pc.close();
  }
}
