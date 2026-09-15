import { afterEach, describe, expect, it } from "vitest";

import { clearRoomSession, loadRoomSession, saveRoomSession } from "./room-persistence";

function session(overrides: Partial<Parameters<typeof saveRoomSession>[0]> = {}) {
  return {
    roomId: "room-1",
    token: "tok-1",
    code: "1-apple-bear-cat",
    nameplate: 1,
    expiresAt: new Date(Date.now() + 60_000).toISOString(),
    role: "creator" as const,
    ...overrides,
  };
}

afterEach(() => {
  clearRoomSession();
});

describe("room-persistence", () => {
  it("has nothing to load when no session was saved", () => {
    expect(loadRoomSession()).toBeNull();
  });

  it("round-trips a saved session", () => {
    const s = session();
    saveRoomSession(s);
    expect(loadRoomSession()).toEqual(s);
  });

  it("clearRoomSession removes it", () => {
    saveRoomSession(session());
    clearRoomSession();
    expect(loadRoomSession()).toBeNull();
  });

  it("treats an already-expired session as absent", () => {
    saveRoomSession(session({ expiresAt: new Date(Date.now() - 1000).toISOString() }));
    expect(loadRoomSession()).toBeNull();
  });

  it("treats malformed JSON in storage as absent", () => {
    sessionStorage.setItem("beam:room-session", "{not json");
    expect(loadRoomSession()).toBeNull();
  });

  it("treats a value missing required fields as absent", () => {
    sessionStorage.setItem("beam:room-session", JSON.stringify({ roomId: "room-1" }));
    expect(loadRoomSession()).toBeNull();
  });

  it("a later save overwrites an earlier one", () => {
    saveRoomSession(session({ roomId: "room-1" }));
    saveRoomSession(session({ roomId: "room-2" }));
    expect(loadRoomSession()?.roomId).toBe("room-2");
  });
});
