import { useState } from "react";

import { QrCode } from "../components/QrCode";
import { formatSas } from "../engine/sas";
import type { SessionPhase } from "../engine/session";
import { useSessionStore } from "../state/session-store";

interface RoomPageProps {
  /** The code from a share link's `#fragment`, if this page was opened that way. */
  joinCode: string | null;
  onLeave: () => void;
}

function shareLink(code: string): string {
  return `${window.location.origin}/r#${code}`;
}

const STATUS_TEXT: Record<SessionPhase, string> = {
  idle: "",
  creating: "Creating room…",
  joining: "Joining room…",
  waiting_for_peer: "Waiting for the other device to join…",
  connecting: "Connecting…",
  connected: "Connected",
  peer_left: "The other device disconnected.",
  failed: "Something went wrong.",
};

export default function RoomPage({ joinCode, onLeave }: RoomPageProps) {
  const state = useSessionStore();
  const [joining, setJoining] = useState(false);

  if (state.phase === "idle" && joinCode) {
    return (
      <main className="mx-auto flex min-h-svh max-w-md flex-col items-center justify-center gap-6 px-4 py-12 text-center">
        <h1 className="text-2xl font-semibold">Join this Beam room?</h1>
        <p className="font-mono text-lg">{joinCode}</p>
        <button
          type="button"
          className="rounded-lg bg-indigo-600 px-6 py-3 font-medium text-white disabled:opacity-50"
          disabled={joining}
          onClick={async () => {
            setJoining(true);
            await state.joinRoom(joinCode);
            setJoining(false);
          }}
        >
          {joining ? "Joining…" : "Join room"}
        </button>
      </main>
    );
  }

  if (state.phase === "idle") {
    onLeave();
    return null;
  }

  const code = state.room?.code;

  return (
    <main className="mx-auto max-w-md px-4 py-12">
      <h1 className="text-2xl font-semibold">Beam</h1>
      <p className="mt-2 text-slate-500">{STATUS_TEXT[state.phase]}</p>

      {code && (
        <section className="mt-6 rounded-xl border border-slate-200 p-4 text-center dark:border-slate-800">
          <p className="text-sm text-slate-500">Share this with the other device</p>
          <p className="mt-1 font-mono text-lg">{code}</p>
          <div className="mt-4 flex justify-center">
            <QrCode value={shareLink(code)} />
          </div>
        </section>
      )}

      {state.phase === "connected" && state.sas && (
        <section className="mt-6 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-center dark:border-emerald-800 dark:bg-emerald-950">
          <p className="text-sm font-medium text-emerald-800 dark:text-emerald-300">
            Read these symbols aloud and check they match on the other device:
          </p>
          <p className="mt-2 text-2xl">{formatSas(state.sas)}</p>
          <p className="mt-2 text-xs text-slate-500">
            If they don't match exactly, stop -- something may be intercepting the connection.
          </p>
          <p className="mt-4 text-sm text-slate-500">File transfer is coming in Milestone 8.</p>
        </section>
      )}

      {state.phase === "failed" && (
        <p className="mt-6 rounded-lg bg-red-50 p-4 text-red-700 dark:bg-red-950 dark:text-red-300">{state.error}</p>
      )}

      <button
        type="button"
        className="mt-8 text-sm text-slate-500 underline"
        onClick={() => {
          state.leave();
          onLeave();
        }}
      >
        Leave room
      </button>
    </main>
  );
}
