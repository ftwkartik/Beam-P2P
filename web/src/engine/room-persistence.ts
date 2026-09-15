/**
 * Persists just enough of a room session across a page reload to resume it
 * automatically (docs/architecture.md §4.3: a receiver reload is expected to
 * resume, not force the user to re-enter the room code). `sessionStorage`, not
 * `localStorage`: this is a live session, not something that should silently
 * reattach in a brand-new tab days later, and it's cleared the moment the tab
 * closes. The room token itself (from `docs/architecture.md`: "valid for the
 * room's lifetime") is what makes a direct WS reconnect possible, skipping the
 * REST create/join step entirely.
 */

import type { Role } from "../protocol/generated/server-message";

export interface PersistedRoomSession {
  roomId: string;
  token: string;
  code: string | null;
  nameplate: number | null;
  expiresAt: string | number;
  role: Role;
}

const STORAGE_KEY = "beam:room-session";

function hasSessionStorage(): boolean {
  return typeof sessionStorage !== "undefined";
}

export function saveRoomSession(session: PersistedRoomSession): void {
  if (!hasSessionStorage()) return;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Storage full, disabled or unavailable (private browsing in some browsers):
    // resume-after-reload just won't be offered. Nothing else depends on this.
  }
}

/** Returns the persisted session, or null if there is none, it's malformed, or it
 * has already expired -- callers don't need to separately re-check `expiresAt`. */
export function loadRoomSession(): PersistedRoomSession | null {
  if (!hasSessionStorage()) return null;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isPersistedRoomSession(parsed)) return null;
    if (new Date(parsed.expiresAt).getTime() <= Date.now()) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function clearRoomSession(): void {
  if (!hasSessionStorage()) return;
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to recover from: worst case a stale entry lingers until it expires.
  }
}

function isPersistedRoomSession(value: unknown): value is PersistedRoomSession {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.roomId === "string" &&
    typeof v.token === "string" &&
    (typeof v.code === "string" || v.code === null) &&
    (typeof v.nameplate === "number" || v.nameplate === null) &&
    (typeof v.expiresAt === "string" || typeof v.expiresAt === "number") &&
    (v.role === "creator" || v.role === "joiner")
  );
}
