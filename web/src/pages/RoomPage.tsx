import { useState } from "react";

import { Logo } from "../components/Logo";
import { QrCode } from "../components/QrCode";
import { TransferPanel } from "../components/TransferPanel";
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
        <div className="w-full rounded-lg border-2 border-stone-900 bg-parchment p-8 shadow-[4px_4px_0_0_theme(colors.stone.900)] dark:border-stone-200 dark:bg-parchment-dark dark:shadow-[4px_4px_0_0_theme(colors.stone.200)]">
          <h1 className="text-xl font-semibold text-stone-900 dark:text-stone-100">Join this Beam room?</h1>
          <p className="mt-3 font-mono text-lg text-green-800 dark:text-green-400" data-testid="room-code">
            {joinCode}
          </p>
          <button
            type="button"
            className="mt-6 w-full rounded-md border-2 border-green-900 bg-green-600 px-6 py-3 font-medium text-white transition-colors hover:bg-green-700 disabled:opacity-50 dark:border-green-400"
            disabled={joining}
            onClick={async () => {
              setJoining(true);
              await state.joinRoom(joinCode);
              setJoining(false);
            }}
          >
            {joining ? "Joining…" : "Join room"}
          </button>
        </div>
      </main>
    );
  }

  if (state.phase === "idle") {
    onLeave();
    return null;
  }

  const code = state.room?.code;

  return (
    <main className="mx-auto max-w-lg px-4 py-12">
      <div className="rounded-lg border-2 border-stone-900 bg-parchment p-6 shadow-[4px_4px_0_0_theme(colors.stone.900)] dark:border-stone-200 dark:bg-parchment-dark dark:shadow-[4px_4px_0_0_theme(colors.stone.200)]">
        <div className="text-center">
          <Logo size="sm" heading />
          <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">{STATUS_TEXT[state.phase]}</p>
        </div>

        {code && (
          <section className="mt-6 rounded-md border-2 border-stone-800 bg-white p-4 text-center dark:border-stone-300 dark:bg-stone-900">
            <p className="text-sm text-stone-600 dark:text-stone-400">Share this with the other device</p>
            <p className="mt-1 font-mono text-lg text-stone-900 dark:text-stone-100" data-testid="room-code">
              {code}
            </p>
            <div className="mt-4 flex justify-center">
              <QrCode value={shareLink(code)} />
            </div>
          </section>
        )}

        {state.phase === "connected" && state.sas && (
          <section className="mt-4 rounded-md border-2 border-green-800 bg-green-50 p-4 text-center dark:border-green-400 dark:bg-green-950">
            <p className="text-sm font-medium text-green-900 dark:text-green-300">
              Read these symbols aloud and check they match on the other device:
            </p>
            <p className="mt-2 text-2xl" data-testid="sas-phrase">
              {formatSas(state.sas)}
            </p>
            <p className="mt-2 text-xs text-stone-600 dark:text-stone-400">
              If they don't match exactly, stop -- something may be intercepting the connection.
            </p>
          </section>
        )}

        {state.phase === "connected" && <TransferPanel />}

        {state.phase === "failed" && (
          <p className="mt-4 rounded-md border-2 border-red-800 bg-red-50 p-4 text-red-700 dark:border-red-400 dark:bg-red-950 dark:text-red-300">
            {state.error}
          </p>
        )}

        <button
          type="button"
          className="mt-8 text-sm text-stone-500 underline hover:text-green-700 dark:text-stone-400 dark:hover:text-green-400"
          onClick={() => {
            state.leave();
            onLeave();
          }}
        >
          Leave room
        </button>
      </div>
    </main>
  );
}
