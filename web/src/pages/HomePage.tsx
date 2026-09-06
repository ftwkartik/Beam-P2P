import { useMemo, useState } from "react";

import { suggestCompletions } from "../engine/codes";
import { useSessionStore } from "../state/session-store";

interface HomePageProps {
  onRoomReady: (code: string) => void;
}

/** Suggestions for the token currently being typed (the last whitespace/hyphen-
 * separated word), so users don't have to spell out the whole wordlist word
 * (docs/protocol.md §1, "autocompletion against the wordlist in the UI and CLI"). */
function useWordSuggestions(value: string): string[] {
  return useMemo(() => {
    const lastToken = value.split(/[\s-]+/).at(-1) ?? "";
    if (lastToken.length < 2) return [];
    return suggestCompletions(lastToken, 5);
  }, [value]);
}

export default function HomePage({ onRoomReady }: HomePageProps) {
  const createRoom = useSessionStore((s) => s.createRoom);
  const joinRoom = useSessionStore((s) => s.joinRoom);

  const [creating, setCreating] = useState(false);
  const [joining, setJoining] = useState(false);
  const [code, setCode] = useState("");
  const [joinError, setJoinError] = useState<string | null>(null);

  const suggestions = useWordSuggestions(code);

  async function handleCreate(): Promise<void> {
    setCreating(true);
    await createRoom();
    setCreating(false);
    const roomCode = useSessionStore.getState().room?.code;
    if (roomCode) onRoomReady(roomCode);
  }

  async function handleJoin(): Promise<void> {
    setJoining(true);
    setJoinError(null);
    await joinRoom(code);
    setJoining(false);
    const state = useSessionStore.getState();
    if (state.phase === "failed") {
      setJoinError(state.error);
      return;
    }
    onRoomReady(code);
  }

  function applySuggestion(word: string): void {
    const tokens = code.split(/[\s-]+/).filter(Boolean);
    tokens[tokens.length - 1] = word;
    setCode(tokens.join("-") + "-");
  }

  return (
    <main className="mx-auto flex min-h-svh max-w-md flex-col justify-center gap-10 px-4 py-12">
      <div className="text-center">
        <h1 className="text-3xl font-semibold">Beam</h1>
        <p className="mt-2 text-slate-500">Send files directly between two devices. Nothing touches a server.</p>
      </div>

      <section className="rounded-xl border border-slate-200 p-6 dark:border-slate-800">
        <h2 className="font-medium">Send a file</h2>
        <p className="mt-1 text-sm text-slate-500">Create a room and share the code with the other device.</p>
        <button
          type="button"
          className="mt-4 w-full rounded-lg bg-indigo-600 py-3 font-medium text-white disabled:opacity-50"
          disabled={creating}
          onClick={() => void handleCreate()}
        >
          {creating ? "Creating…" : "Create a room"}
        </button>
      </section>

      <section className="rounded-xl border border-slate-200 p-6 dark:border-slate-800">
        <h2 className="font-medium">Receive a file</h2>
        <p className="mt-1 text-sm text-slate-500">Enter the code shown on the other device.</p>
        <form
          className="mt-4 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void handleJoin();
          }}
        >
          <input
            type="text"
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="7-otter-lantern-tiger"
            className="rounded-lg border border-slate-300 px-3 py-2 font-mono dark:border-slate-700 dark:bg-slate-900"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((word) => (
                <button
                  key={word}
                  type="button"
                  className="rounded-full bg-slate-100 px-2.5 py-1 text-xs dark:bg-slate-800"
                  onClick={() => applySuggestion(word)}
                >
                  {word}
                </button>
              ))}
            </div>
          )}
          {joinError && <p className="text-sm text-red-600 dark:text-red-400">{joinError}</p>}
          <button
            type="submit"
            className="mt-2 rounded-lg border border-slate-300 py-3 font-medium disabled:opacity-50 dark:border-slate-700"
            disabled={joining || code.trim().length === 0}
          >
            {joining ? "Joining…" : "Join room"}
          </button>
        </form>
      </section>
    </main>
  );
}
