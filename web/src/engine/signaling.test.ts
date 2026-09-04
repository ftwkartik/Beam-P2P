import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignalingClient } from "./signaling";

/** A minimal fake WebSocket good enough to drive SignalingClient's event handlers. */
class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = 0;
  sent: string[] = [];
  url: string;
  private listeners: Record<string, ((event: unknown) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, handler: (event: unknown) => void): void {
    (this.listeners[type] ??= []).push(handler);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(code = 1000): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", { code, reason: "" });
  }

  emit(type: string, event: unknown): void {
    for (const handler of this.listeners[type] ?? []) handler(event);
  }

  simulateOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open", {});
  }

  simulateMessage(data: unknown): void {
    this.emit("message", { data: JSON.stringify(data) });
  }

  simulateClose(code: number, reason = ""): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", { code, reason });
  }
}

function factory(url: string): WebSocket {
  return new FakeWebSocket(url) as unknown as WebSocket;
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

function latestSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances.at(-1);
  if (!socket) throw new Error("no socket created");
  return socket;
}

describe("SignalingClient", () => {
  it("sends hello with the token as soon as the socket opens", () => {
    const client = new SignalingClient({
      wsUrl: "wss://example.test/ws",
      token: "tok-123",
      clientVersion: "1.0.0",
      webSocketFactory: factory,
    });
    client.connect();
    latestSocket().simulateOpen();

    const sent = JSON.parse(latestSocket().sent[0]);
    expect(sent).toEqual({
      type: "hello",
      v: 1,
      token: "tok-123",
      client: { kind: "web", version: "1.0.0" },
    });
  });

  it("dispatches welcome, peer_joined, peer_left and signal to their callbacks", () => {
    const onWelcome = vi.fn();
    const onPeerJoined = vi.fn();
    const onPeerLeft = vi.fn();
    const onSignal = vi.fn();

    const client = new SignalingClient({
      wsUrl: "wss://example.test/ws",
      token: "tok",
      clientVersion: "1.0.0",
      webSocketFactory: factory,
      onWelcome,
      onPeerJoined,
      onPeerLeft,
      onSignal,
    });
    client.connect();
    const socket = latestSocket();
    socket.simulateOpen();

    socket.simulateMessage({
      type: "welcome",
      peer_id: "p1",
      room_id: "r1",
      role: "creator",
      polite: false,
      peers: [],
      expires_at: "2026-01-01T00:00:00Z",
    });
    expect(onWelcome).toHaveBeenCalledOnce();

    socket.simulateMessage({ type: "peer_joined", peer_id: "p2", role: "joiner" });
    expect(onPeerJoined).toHaveBeenCalledOnce();

    socket.simulateMessage({
      type: "signal",
      from: "p2",
      data: { kind: "candidate", candidate: null },
    });
    expect(onSignal).toHaveBeenCalledOnce();

    socket.simulateMessage({ type: "peer_left", peer_id: "p2", reason: "left" });
    expect(onPeerLeft).toHaveBeenCalledOnce();
  });

  it("reconnects with backoff after an unexpected close", () => {
    const onStatusChange = vi.fn();
    const client = new SignalingClient({
      wsUrl: "wss://example.test/ws",
      token: "tok",
      clientVersion: "1.0.0",
      webSocketFactory: factory,
      onStatusChange,
    });
    client.connect();
    latestSocket().simulateOpen();
    expect(FakeWebSocket.instances).toHaveLength(1);

    latestSocket().simulateClose(1006, "abnormal");
    expect(onStatusChange).toHaveBeenCalledWith("reconnecting");

    vi.advanceTimersByTime(300);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("does not reconnect after a fatal close code", () => {
    const onFatal = vi.fn();
    const client = new SignalingClient({
      wsUrl: "wss://example.test/ws",
      token: "tok",
      clientVersion: "1.0.0",
      webSocketFactory: factory,
      onFatal,
    });
    client.connect();
    latestSocket().simulateOpen();

    latestSocket().simulateClose(4401, "bad token");
    expect(onFatal).toHaveBeenCalledWith(4401, "bad token");

    vi.advanceTimersByTime(10_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after a manual close", () => {
    const client = new SignalingClient({
      wsUrl: "wss://example.test/ws",
      token: "tok",
      clientVersion: "1.0.0",
      webSocketFactory: factory,
    });
    client.connect();
    latestSocket().simulateOpen();

    client.close();
    expect(latestSocket().sent.some((m) => JSON.parse(m).type === "leave")).toBe(true);

    vi.advanceTimersByTime(10_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
