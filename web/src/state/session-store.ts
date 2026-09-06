/**
 * Thin Zustand adapter over the framework-free BeamSession state machine
 * (docs/adr/007-react-typescript-client.md). One session per page load: Beam is a
 * single-purpose two-peer transfer tool, not a multi-room client.
 */

import { create } from "zustand";

import { BeamSession, type SessionState } from "../engine/session";

const session = new BeamSession();

export interface SessionStore extends SessionState {
  createRoom: () => Promise<void>;
  joinRoom: (code: string) => Promise<void>;
  leave: () => void;
}

export const useSessionStore = create<SessionStore>((set) => {
  session.subscribe((state) => set(state));
  return {
    ...session.getState(),
    createRoom: () => session.createRoom(),
    joinRoom: (code: string) => session.joinRoom(code),
    leave: () => session.leave(),
  };
});
