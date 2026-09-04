/**
 * Signaling WebSocket client (docs/protocol.md §3). Framework-free and independently
 * testable (docs/adr/007-react-typescript-client.md): it knows nothing about React or
 * Zustand, only about the wire protocol and reconnecting when the connection drops.
 */

import type { ClientMessage } from "../protocol/generated/client-message";
import type {
  PeerJoinedMessage,
  PeerLeftMessage,
  PeerReconnectingMessage,
  ServerErrorMessage,
  ServerMessage,
  ServerSignalMessage,
  WelcomeMessage,
} from "../protocol/generated/server-message";

/** Close codes that mean "don't bother reconnecting" (docs/protocol.md §3, "Close codes").
 * Everything else (network drops, 4408 hello timeout, 4429 rate limit, ...) is retried. */
const FATAL_CLOSE_CODES = new Set([4401, 4403, 4404, 4409, 4413]);

const INITIAL_BACKOFF_MS = 300;
const MAX_BACKOFF_MS = 5000;

export type SignalingStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface SignalingClientOptions {
  wsUrl: string;
  token: string;
  clientVersion: string;
  onStatusChange?: (status: SignalingStatus) => void;
  onWelcome?: (message: WelcomeMessage) => void;
  onPeerJoined?: (message: PeerJoinedMessage) => void;
  onPeerReconnecting?: (message: PeerReconnectingMessage) => void;
  onPeerLeft?: (message: PeerLeftMessage) => void;
  onSignal?: (message: ServerSignalMessage) => void;
  onServerError?: (message: ServerErrorMessage) => void;
  /** The connection is gone for good (a fatal close code, or `close()` was called). */
  onFatal?: (code: number, reason: string) => void;
  /** Constructs the WebSocket; overridable in tests. Defaults to the global `WebSocket`. */
  webSocketFactory?: (url: string) => WebSocket;
}

export class SignalingClient {
  private readonly options: SignalingClientOptions;
  private socket: WebSocket | null = null;
  private status: SignalingStatus = "closed";
  private manualClose = false;
  private backoffMs = INITIAL_BACKOFF_MS;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: SignalingClientOptions) {
    this.options = options;
  }

  connect(): void {
    this.manualClose = false;
    this.open();
  }

  /** Gracefully leaves (server publishes `peer_left` immediately) and stops retrying. */
  close(): void {
    this.manualClose = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket !== null && this.socket.readyState === WebSocket.OPEN) {
      this.send({ type: "leave" });
    }
    this.socket?.close(1000);
    this.socket = null;
    this.setStatus("closed");
  }

  send(message: ClientMessage): void {
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) {
      console.warn("beam: dropped signaling message, socket not open", message.type);
      return;
    }
    this.socket.send(JSON.stringify(message));
  }

  private open(): void {
    this.setStatus(this.backoffMs === INITIAL_BACKOFF_MS ? "connecting" : "reconnecting");

    const factory = this.options.webSocketFactory ?? ((url: string) => new WebSocket(url));
    const socket = factory(this.options.wsUrl);
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.backoffMs = INITIAL_BACKOFF_MS;
      this.setStatus("open");
      this.send({
        type: "hello",
        v: 1,
        token: this.options.token,
        client: { kind: "web", version: this.options.clientVersion },
      });
    });

    socket.addEventListener("message", (event: MessageEvent) => {
      this.handleMessage(event.data as string);
    });

    socket.addEventListener("close", (event: CloseEvent) => {
      this.socket = null;
      if (this.manualClose) {
        return;
      }
      if (FATAL_CLOSE_CODES.has(event.code)) {
        this.setStatus("closed");
        this.options.onFatal?.(event.code, event.reason);
        return;
      }
      this.scheduleReconnect();
    });
  }

  private scheduleReconnect(): void {
    this.setStatus("reconnecting");
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, this.backoffMs);
    this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
  }

  private handleMessage(raw: string): void {
    let message: ServerMessage;
    try {
      message = JSON.parse(raw);
    } catch {
      console.error("beam: received malformed signaling message", raw);
      return;
    }

    switch (message.type) {
      case "welcome":
        this.options.onWelcome?.(message);
        break;
      case "peer_joined":
        this.options.onPeerJoined?.(message);
        break;
      case "peer_reconnecting":
        this.options.onPeerReconnecting?.(message);
        break;
      case "peer_left":
        this.options.onPeerLeft?.(message);
        break;
      case "signal":
        this.options.onSignal?.(message);
        break;
      case "error":
        this.options.onServerError?.(message);
        break;
      default:
        console.warn("beam: unknown signaling message type", message);
    }
  }

  private setStatus(status: SignalingStatus): void {
    if (status === this.status) return;
    this.status = status;
    this.options.onStatusChange?.(status);
  }

  getStatus(): SignalingStatus {
    return this.status;
  }
}
