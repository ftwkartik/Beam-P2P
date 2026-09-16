/**
 * Orchestrates a Beam session: REST room lifecycle + signaling + WebRTC peer
 * connection + SAS derivation, as one framework-free, independently testable state
 * machine (docs/adr/007-react-typescript-client.md). `state/session-store.ts` is the
 * thin Zustand adapter React actually subscribes to.
 */

import * as realApi from "./api";
import { PeerConnection, type PeerConnectionOptions } from "./peer";
import { clearRoomSession, loadRoomSession, saveRoomSession } from "./room-persistence";
import { deriveSas, type SasSymbol } from "./sas";
import { SignalingClient, type SignalingClientOptions, type SignalingStatus } from "./signaling";
import { MemoryStorage } from "./storage/memory-storage";
import { OpfsStorage } from "./storage/opfs-storage";
import type { TransferStorage } from "./storage/types";
import type {
  FileReadyResult,
  OfferedManifest,
  ReceiverPhase,
  ReceiverProgress,
} from "./transfer/receiver";
import { TransferReceiver } from "./transfer/receiver";
import type { DataChannelLike } from "./transfer/channels";
import { IndexedDbResumeStore, type ResumeStore } from "./transfer/resume-store";
import type { SenderPhase, SenderProgress } from "./transfer/sender";
import { TransferSender } from "./transfer/sender";
import type { PeerMessage } from "../protocol/generated/peer-message";
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

export interface TransferRate {
  bytesPerSecond: number;
  etaSeconds: number;
}

export interface OutgoingTransferState {
  phase: SenderPhase;
  progress: SenderProgress | null;
  rate: TransferRate | null;
  error: string | null;
}

export interface IncomingTransferState {
  phase: ReceiverPhase;
  progress: ReceiverProgress | null;
  rate: TransferRate | null;
  error: string | null;
}

/** A simple whole-transfer-average throughput/ETA, computed here (not in a React
 * component) so the UI layer only ever reads plain numbers -- no timers, refs or
 * effects of its own (docs/adr/007-react-typescript-client.md: engine stays
 * framework-free, components stay thin). */
function computeRate(startedAt: number, bytesDone: number, totalBytes: number): TransferRate {
  const elapsedSeconds = (Date.now() - startedAt) / 1000;
  const bytesPerSecond = elapsedSeconds > 0.5 ? bytesDone / elapsedSeconds : 0;
  const remaining = totalBytes - bytesDone;
  const etaSeconds = bytesPerSecond > 0 ? remaining / bytesPerSecond : Number.POSITIVE_INFINITY;
  return { bytesPerSecond, etaSeconds };
}

export interface SessionState {
  phase: SessionPhase;
  error: string | null;
  room: RoomInfo | null;
  otherPeer: PeerInfo | null;
  sas: SasSymbol[] | null;
  signalingStatus: SignalingStatus;
  outgoingTransfer: OutgoingTransferState | null;
  /** A manifest the other peer offered, awaiting accept()/decline(). */
  incomingOffer: OfferedManifest | null;
  incomingTransfer: IncomingTransferState | null;
  /** Files the receiver has fully verified and can now be saved. */
  readyFiles: FileReadyResult[];
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
  storageFactory?: (transferId: string) => TransferStorage;
  resumeStore?: ResumeStore;
}

function initialState(): SessionState {
  return {
    phase: "idle",
    error: null,
    room: null,
    otherPeer: null,
    sas: null,
    signalingStatus: "closed",
    outgoingTransfer: null,
    incomingOffer: null,
    incomingTransfer: null,
    readyFiles: [],
  };
}

/** OPFS sync access handles are the common case in modern browsers; MemoryStorage is
 * the documented fallback for the rest (docs/adr/006-receiver-storage-opfs.md). This
 * checks for OPFS root access as a proxy for full support rather than actually trying
 * to open a sync access handle, which needs a round trip to the worker to find out. */
function defaultStorageFactory(transferId: string): TransferStorage {
  const opfsAvailable = typeof navigator !== "undefined" && typeof navigator.storage?.getDirectory === "function";
  return opfsAvailable ? new OpfsStorage(transferId) : new MemoryStorage();
}

export class BeamSession {
  private readonly api: ApiClient;
  private readonly signalingFactory: (options: SignalingClientOptions) => SignalingClient;
  private readonly peerConnectionFactory: (options: PeerConnectionOptions) => PeerConnection;
  private readonly storageFactory: (transferId: string) => TransferStorage;
  private readonly resumeStore: ResumeStore;

  private state: SessionState = initialState();
  private readonly listeners = new Set<(state: SessionState) => void>();

  private signaling: SignalingClient | null = null;
  private peerConnection: PeerConnection | null = null;
  private token: string | null = null;
  private polite = false;
  private iceServers: RTCIceServer[] = [];

  private sender: TransferSender | null = null;
  private receiver: TransferReceiver | null = null;
  /** An outgoing transfer that hasn't reached a terminal phase yet, tracked
   * independently of `sender` so it survives `setupPeerConnection()` wiping the
   * sender on a reconnect -- once the new connection's control channel reopens
   * (see `onControlChannelOpen` below), it's restarted with the same transfer id
   * and files, and the receiver resumes it via its own persisted bitmap
   * (transfer/receiver.ts). */
  private pendingOutgoingTransfer: { transferId: string; files: File[] } | null = null;

  constructor(options: BeamSessionOptions = {}) {
    this.api = options.api ?? realApi;
    this.signalingFactory = options.signalingFactory ?? ((o) => new SignalingClient(o));
    this.peerConnectionFactory = options.peerConnectionFactory ?? ((o) => new PeerConnection(o));
    this.storageFactory = options.storageFactory ?? defaultStorageFactory;
    this.resumeStore = options.resumeStore ?? new IndexedDbResumeStore();
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
    this.sender = null;
    this.receiver = null;
    this.pendingOutgoingTransfer = null;
    this.token = null;
    clearRoomSession();
    this.setState(initialState());
  }

  /** Reconnects to a room persisted before a page reload (`room-persistence.ts`),
   * skipping the REST create/join step -- the room token is valid for the room's
   * whole lifetime. Returns whether a persisted session was found at all; a
   * connection failure past that point surfaces the normal way, via `state.phase`
   * becoming "failed". Call once, on app boot, before the user does anything else. */
  async resume(): Promise<boolean> {
    const persisted = loadRoomSession();
    if (!persisted) return false;

    this.token = persisted.token;
    this.setState({ ...initialState(), phase: "connecting" });
    await this.startSignaling({
      roomId: persisted.roomId,
      code: persisted.code,
      nameplate: persisted.nameplate,
      expiresAt: persisted.expiresAt,
      role: persisted.role,
      peerId: "",
    });
    return true;
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
    saveRoomSession({
      roomId: room.roomId,
      token: this.token,
      code: room.code,
      nameplate: room.nameplate,
      expiresAt: room.expiresAt,
      role: room.role,
    });

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
    this.sender = null;
    this.receiver = null;
    this.setState({ otherPeer: null, sas: null, phase: "peer_left" });
  }

  private setupPeerConnection(): void {
    this.peerConnection?.close();
    this.sender = null;
    this.receiver = null;
    this.peerConnection = this.peerConnectionFactory({
      polite: this.polite,
      iceServers: this.iceServers,
      onSendSignal: (data) => this.signaling?.send({ type: "signal", data }),
      onConnectionStateChange: (rtcState) => {
        if (rtcState === "connected") void this.handleConnected();
      },
      // `connectionstatechange` reaching "connected" does not imply the negotiated
      // data channels have themselves reached "open" yet -- calling send() before
      // that throws. A resumed outgoing transfer restarts here, once the control
      // channel is actually usable, rather than racing that from handleConnected().
      // The very first connection on a session never hit this race in practice
      // (there's always a real user pause before anyone picks a file to send), which
      // is exactly why this needed a real reconnect to surface (Milestone 9).
      onControlChannelOpen: () => {
        if (this.pendingOutgoingTransfer && this.sender === null) {
          this.startOutgoingTransfer(this.pendingOutgoingTransfer.transferId, this.pendingOutgoingTransfer.files);
        }
      },
    });

    this.peerConnection.controlChannel.addEventListener("message", (event: MessageEvent<string>) => {
      try {
        this.handleIncomingControlMessage(JSON.parse(event.data) as PeerMessage);
      } catch {
        // A malformed control message from the peer: nothing to recover, just drop it.
      }
    });
    this.peerConnection.dataChannel.addEventListener("message", (event: MessageEvent<ArrayBuffer>) => {
      this.receiver?.handleDataFrame(new Uint8Array(event.data));
    });
  }

  private handleIncomingControlMessage(message: PeerMessage): void {
    if (message.type === "offer_files" && this.receiver === null) {
      this.createReceiver(message.transfer_id);
    }
    this.sender?.handleControlMessage(message);
    this.receiver?.handleControlMessage(message);
  }

  private createReceiver(transferId: string): void {
    const startedAt = Date.now();
    const receiver = new TransferReceiver({
      storage: this.storageFactory(transferId),
      control: this.peerConnection!.controlChannel,
      resumeStore: this.resumeStore,
      onOffer: (manifest) => this.setState({ incomingOffer: manifest }),
      onPhaseChange: (phase) =>
        this.setState({
          incomingTransfer: {
            phase,
            progress: this.state.incomingTransfer?.progress ?? null,
            rate: this.state.incomingTransfer?.rate ?? null,
            error: null,
          },
        }),
      onProgress: (progress) =>
        this.setState({
          incomingTransfer: {
            phase: receiver.getPhase(),
            progress,
            rate: computeRate(startedAt, progress.totalBytesVerified, progress.totalBytes),
            error: null,
          },
        }),
      onFileReady: (result) => this.setState({ readyFiles: [...this.state.readyFiles, result] }),
      onError: (error) =>
        this.setState({
          incomingTransfer: {
            phase: receiver.getPhase(),
            progress: this.state.incomingTransfer?.progress ?? null,
            rate: this.state.incomingTransfer?.rate ?? null,
            error,
          },
        }),
    });
    this.receiver = receiver;
  }

  /** Offers `files` to the connected peer. Requires an already-connected session. */
  sendFiles(files: File[]): void {
    if (!this.peerConnection) return;
    const transferId = crypto.randomUUID();
    this.pendingOutgoingTransfer = { transferId, files };
    this.startOutgoingTransfer(transferId, files);
  }

  /** Starts (or, after a reconnect, restarts) sending `files` under `transferId`.
   * Reusing the same id on a restart is what lets the receiver's persisted bitmap
   * (transfer/receiver.ts) match it up and skip already-verified blocks. */
  private startOutgoingTransfer(transferId: string, files: File[]): void {
    if (!this.peerConnection) return;
    const startedAt = Date.now();
    const sender = new TransferSender({
      transferId,
      files,
      control: this.peerConnection.controlChannel,
      // RTCDataChannel.send is overloaded (string | Blob | ArrayBuffer |
      // ArrayBufferView); TS's overload-to-single-signature assignability check
      // doesn't resolve that a Uint8Array argument matches the ArrayBufferView
      // overload here (the same @types/node/TS TypedArray-generics friction as
      // elsewhere -- see memory-storage.ts's comment), even though it works at runtime.
      data: this.peerConnection.dataChannel as unknown as DataChannelLike,
      onPhaseChange: (phase) => {
        this.setState({
          outgoingTransfer: {
            phase,
            progress: this.state.outgoingTransfer?.progress ?? null,
            rate: this.state.outgoingTransfer?.rate ?? null,
            error: null,
          },
        });
        if (isSenderTerminalPhase(phase)) this.pendingOutgoingTransfer = null;
      },
      onProgress: (progress) =>
        this.setState({
          outgoingTransfer: {
            phase: sender.getPhase(),
            progress,
            rate: computeRate(startedAt, progress.totalBytesSent, progress.totalBytes),
            error: null,
          },
        }),
      onError: (error) =>
        this.setState({
          outgoingTransfer: {
            phase: sender.getPhase(),
            progress: this.state.outgoingTransfer?.progress ?? null,
            rate: this.state.outgoingTransfer?.rate ?? null,
            error,
          },
        }),
    });
    this.sender = sender;
    sender.start();
  }

  /** Accepts the pending incoming offer (see `state.incomingOffer`). */
  acceptIncomingTransfer(): void {
    void this.receiver?.accept();
    this.setState({ incomingOffer: null });
  }

  declineIncomingTransfer(reason = "Declined by the recipient."): void {
    this.receiver?.decline(reason);
    this.setState({ incomingOffer: null });
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
    this.sender = null;
    this.receiver = null;
    this.pendingOutgoingTransfer = null;
    clearRoomSession();
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

function isSenderTerminalPhase(phase: SenderPhase): boolean {
  return phase === "completed" || phase === "failed" || phase === "cancelled" || phase === "declined";
}
