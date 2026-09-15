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
  /** Reconnects to a room persisted before a page reload, if there is one. Call once
   * on app boot; returns whether a persisted session was found. */
  resume: () => Promise<boolean>;
  leave: () => void;
  sendFiles: (files: File[]) => void;
  acceptIncomingTransfer: () => void;
  declineIncomingTransfer: (reason?: string) => void;
}

export const useSessionStore = create<SessionStore>((set) => {
  session.subscribe((state) => set(state));
  return {
    ...session.getState(),
    createRoom: () => session.createRoom(),
    joinRoom: (code: string) => session.joinRoom(code),
    resume: () => session.resume(),
    leave: () => session.leave(),
    sendFiles: (files: File[]) => session.sendFiles(files),
    acceptIncomingTransfer: () => session.acceptIncomingTransfer(),
    declineIncomingTransfer: (reason?: string) => session.declineIncomingTransfer(reason),
  };
});
