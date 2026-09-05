/**
 * REST client for the room lifecycle endpoints (docs/protocol.md §2). `VITE_API_URL`
 * and `VITE_WS_URL` default to empty, meaning same-origin: nginx proxies `/api` and
 * `/ws` to the server in every deployment (infra/nginx.conf), so only local
 * `npm run dev` (which doesn't go through nginx) needs to set them.
 */

export interface CreateRoomResponse {
  room_id: string;
  code: string;
  nameplate: number;
  expires_at: number;
  token: string;
}

export interface JoinRoomResponse {
  room_id: string;
  expires_at: number;
  token: string;
}

export interface IceServer {
  urls: string;
  username?: string | null;
  credential?: string | null;
}

export interface IceServersResponse {
  ice_servers: IceServer[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function apiBaseUrl(): string {
  return import.meta.env.VITE_API_URL || "";
}

function wsBaseUrl(): string {
  const configured = import.meta.env.VITE_WS_URL;
  if (configured) return configured;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}`;
}

export function signalingWsUrl(): string {
  return `${wsBaseUrl()}/ws`;
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const envelope = body as ErrorEnvelope | null;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "UNKNOWN",
      envelope?.error?.message ?? "The request failed.",
    );
  }

  return body as T;
}

export function createRoom(): Promise<CreateRoomResponse> {
  return request<CreateRoomResponse>("/rooms", { method: "POST" });
}

export function joinRoom(code: string): Promise<JoinRoomResponse> {
  return request<JoinRoomResponse>("/rooms/join", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function getIceServers(roomId: string, token: string): Promise<IceServersResponse> {
  return request<IceServersResponse>(`/rooms/${encodeURIComponent(roomId)}/ice-servers`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function closeRoom(roomId: string, token: string): Promise<void> {
  return request<void>(`/rooms/${encodeURIComponent(roomId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}
