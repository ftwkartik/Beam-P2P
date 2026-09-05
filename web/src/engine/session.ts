/**
 * Orchestrates a Beam session: REST room lifecycle + signaling + WebRTC peer
 * connection + SAS derivation, as one framework-free, independently testable state
 * machine (docs/adr/007-react-typescript-client.md). `state/session-store.ts` is the
 * thin Zustand adapter React actually subscribes to.
 */

import * as realApi from "./api";
import { PeerConnection, type PeerConnectionOptions } from "./peer";
import { deriveSas, type SasSymbol } from "./sas";
import { SignalingClient, type SignalingClientOptions, type SignalingStatus } from "./signaling";
import type {
  PeerInfo,
  PeerJoinedMessage,
  PeerLeftMessage,
  PeerReconnectingMessage,
  Role,
  WelcomeMessage,
} from "../protocol/generated/server-message";

export type SessionPhase =
  | "idle"
  | "creating"
  | "joining"
  | "waiting_for_peer"
  | "connecting"
  | "connected"
  | "peer_left"
  | "failed";

export interface RoomInfo {
  roomId: string;
  code: string | null;
  nameplate: number | null;
  expiresAt: string | number;
  role: Role;
  peerId: string;
}

export interface SessionState {
  phase: SessionPhase;
  error: string | null;
  room: RoomInfo | null;
  otherPeer: PeerInfo | null;
  sas: SasSymbol[] | null;
  signalingStatus: SignalingStatus;
}

const CLIENT_VERSION = "0.1.0";

/** The subset of ./api this class calls, injectable for tests. */
export interface ApiClient {
  createRoom: typeof realApi.createRoom;
  joinRoom: typeof realApi.joinRoom;
  getIceServers: typeof realApi.getIceServers;
  signalingWsUrl: typeof realApi.signalingWsUrl;
}

export interface BeamSessionOptions {
  api?: ApiClient;
  signalingFactory?: (options: SignalingClientOptions) => SignalingClient;
  peerConnectionFactory?: (options: PeerConnectionOptions) => PeerConnection;
}

function initialState(): SessionState {
  return {
    phase: "idle",
    error: null,
    room: null,
    otherPeer: null,
    sas: null,
    signalingStatus: "closed",
  };
}

export class BeamSession {
  private readonly api: ApiClient;
  private readonly signalingFactory: (options: SignalingClientOptions) => SignalingClient;
  private readonly peerConnectionFactory: (options: PeerConnectionOptions) => PeerConnection;

  private state: SessionState = initialState();
  private readonly listeners = new Set<(state: SessionState) => void>();

  private signaling: SignalingClient | null = null;
  private peerConnection: PeerConnection | null = null;
  private token: string | null = null;
  private polite = false;
  private iceServers: RTCIceServer[] = [];

  constructor(options: BeamSessionOptions = {}) {
    this.api = options.api ?? realApi;
    this.signalingFactory = options.signalingFactory ?? ((o) => new SignalingClient(o));
    this.peerConnectionFactory = options.peerConnectionFactory ?? ((o) => new PeerConnection(o));
  }

  getState(): SessionState {
    return this.state;
  }

  subscribe(listener: (state: SessionState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async createRoom(): Promise<void> {
    this.setState({ ...initialState(), phase: "creating" });
    try {
      const created = await this.api.createRoom();
      this.token = created.token;
      await this.startSignaling({
        roomId: created.room_id,
        code: created.code,
        nameplate: created.nameplate,
        expiresAt: created.expires_at,
        role: "creator",
        peerId: "",
      });
    } catch (err) {
      this.fail(errorMessage(err));
    }
  }

  async joinRoom(code: string): Promise<void> {
    this.setState({ ...initialState(), phase: "joining" });
    try {
      const joined = await this.api.joinRoom(code);
      this.token = joined.token;
      await this.startSignaling({
        roomId: joined.room_id,
        code,
        nameplate: null,
        expiresAt: joined.expires_at,
        role: "joiner",
        peerId: "",
      });
    } catch (err) {
      this.fail(errorMessage(err));
    }
  }

  leave(): void {
    this.signaling?.close();
    this.peerConnection?.close();
    this.signaling = null;
    this.peerConnection = null;
    this.token = null;
    this.setState(initialState());
  }

  private async startSignaling(room: RoomInfo): Promise<void> {
    if (this.token === null) return;

    const iceResponse = await this.api.getIceServers(room.roomId, this.token);
    this.iceServers = iceResponse.ice_servers.map((s) => ({
      urls: s.urls,
      username: s.username ?? undefined,
      credential: s.credential ?? undefined,
    }));

    this.setState({ room });

    this.signaling = this.signalingFactory({
      wsUrl: this.api.signalingWsUrl(),
      token: this.token,
      clientVersion: CLIENT_VERSION,
      onStatusChange: (status) => this.setState({ signalingStatus: status }),
      onWelcome: (message) => this.handleWelcome(message),
      onPeerJoined: (message) => this.handlePeerJoined(message),
      onPeerReconnecting: (message) => this.handlePeerReconnecting(message),
      onPeerLeft: (message) => this.handlePeerLeft(message),
      onSignal: (message) => {
        void this.peerConnection?.handleSignal(message.data);
      },
      onFatal: (code, reason) => this.fail(`Connection closed (${code}): ${reason || "unknown reason"}`),
    });
    this.signaling.connect();
  }

  private handleWelcome(message: WelcomeMessage): void {
    if (this.state.room === null) return;
    this.polite = message.polite;
    this.setState({
      room: { ...this.state.room, peerId: message.peer_id, role: message.role },
    });

    const other = message.peers.find((p) => p.peer_id !== message.peer_id) ?? null;
    if (other) {
      this.setState({ otherPeer: other, phase: "connecting" });
      this.setupPeerConnection();
    } else {
      this.setState({ phase: "waiting_for_peer" });
    }
  }

  private handlePeerJoined(message: PeerJoinedMessage): void {
    this.setState({
      otherPeer: { peer_id: message.peer_id, role: message.role, state: "online" },
      phase: "connecting",
    });
    this.setupPeerConnection();
  }

  private handlePeerReconnecting(message: PeerReconnectingMessage): void {
    if (this.state.otherPeer?.peer_id !== message.peer_id) return;
    this.setState({ otherPeer: { ...this.state.otherPeer, state: "reconnecting" } });
  }

  private handlePeerLeft(message: PeerLeftMessage): void {
    if (this.state.otherPeer?.peer_id !== message.peer_id) return;
    this.peerConnection?.close();
    this.peerConnection = null;
    this.setState({ otherPeer: null, sas: null, phase: "peer_left" });
  }

  private setupPeerConnection(): void {
    this.peerConnection?.close();
    this.peerConnection = this.peerConnectionFactory({
      polite: this.polite,
      iceServers: this.iceServers,
      onSendSignal: (data) => this.signaling?.send({ type: "signal", data }),
      onConnectionStateChange: (rtcState) => {
        if (rtcState === "connected") void this.handleConnected();
      },
    });
  }

  private async handleConnected(): Promise<void> {
    const fingerprints = this.peerConnection?.getFingerprints();
    if (fingerprints && this.state.room) {
      const sas = await deriveSas(this.state.room.roomId, fingerprints.local, fingerprints.remote);
      this.setState({ sas });
    }
    this.setState({ phase: "connected" });
  }

  private fail(message: string): void {
    this.signaling?.close();
    this.peerConnection?.close();
    this.setState({ phase: "failed", error: message });
  }

  private setState(patch: Partial<SessionState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener(this.state);
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
