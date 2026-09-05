import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, closeRoom, createRoom, getIceServers, joinRoom, signalingWsUrl } from "./api";

function mockFetchOnce(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createRoom", () => {
  it("posts to /api/v1/rooms and returns the parsed body", async () => {
    mockFetchOnce(201, {
      room_id: "r1",
      code: "7-otter-lantern-tiger",
      nameplate: 7,
      expires_at: 123,
      token: "tok",
    });

    const result = await createRoom();
    expect(result.room_id).toBe("r1");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/rooms"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws ApiError with the server's error code on failure", async () => {
    mockFetchOnce(429, { error: { code: "RATE_LIMITED", message: "Too many requests." } });

    await expect(createRoom()).rejects.toMatchObject({
      status: 429,
      code: "RATE_LIMITED",
      message: "Too many requests.",
    });
    await expect(createRoom()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("joinRoom", () => {
  it("sends the code in the request body", async () => {
    mockFetchOnce(200, { room_id: "r1", expires_at: 123, token: "tok" });

    await joinRoom("7-otter-lantern-tiger");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ code: "7-otter-lantern-tiger" });
  });
});

describe("getIceServers", () => {
  it("sends the bearer token", async () => {
    mockFetchOnce(200, { ice_servers: [{ urls: "stun:example.test" }] });

    await getIceServers("r1", "tok-abc");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("/rooms/r1/ice-servers");
    expect(init.headers.Authorization).toBe("Bearer tok-abc");
  });
});

describe("closeRoom", () => {
  it("sends a DELETE request and tolerates an empty body", async () => {
    mockFetchOnce(204, null);
    await expect(closeRoom("r1", "tok")).resolves.toBeFalsy();
  });
});

describe("signalingWsUrl", () => {
  it("derives a ws:// URL from window.location when VITE_WS_URL is unset", () => {
    expect(signalingWsUrl()).toBe("ws://localhost:3000/ws");
  });
});
