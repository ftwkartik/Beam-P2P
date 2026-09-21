import { useMemo, useState } from "react";

import { Logo } from "../components/Logo";
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
    <main className="mx-auto flex min-h-svh max-w-lg flex-col justify-center px-4 py-12">
      <div className="rounded-lg border-2 border-stone-900 bg-parchment p-6 shadow-[4px_4px_0_0_theme(colors.stone.900)] dark:border-stone-200 dark:bg-parchment-dark dark:shadow-[4px_4px_0_0_theme(colors.stone.200)]">
        <div className="text-center">
          <Logo heading />
          <p className="mt-3 text-sm text-stone-600 dark:text-stone-400">
            Send files directly between two devices. Nothing touches a server.
          </p>
        </div>

        <section className="mt-8 rounded-md border-2 border-stone-800 bg-green-50 p-6 dark:border-stone-300 dark:bg-green-950">
          <h2 className="font-medium text-green-900 dark:text-green-200">Send a file</h2>
          <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
            Create a room and share the code with the other device.
          </p>
          <button
            type="button"
            className="mt-4 w-full rounded-md border-2 border-green-900 bg-green-600 py-3 font-medium text-white transition-colors hover:bg-green-700 disabled:opacity-50 dark:border-green-400"
            disabled={creating}
            onClick={() => void handleCreate()}
          >
            {creating ? "Creating…" : "Create a room"}
          </button>
        </section>

        <section className="mt-4 rounded-md border-2 border-stone-800 p-6 dark:border-stone-300">
          <h2 className="font-medium text-stone-900 dark:text-stone-100">Receive a file</h2>
          <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">Enter the code shown on the other device.</p>
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
              className="rounded-md border-2 border-stone-400 bg-white px-3 py-2 font-mono focus:border-green-600 focus:outline-none dark:border-stone-600 dark:bg-stone-900"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            {suggestions.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {suggestions.map((word) => (
                  <button
                    key={word}
                    type="button"
                    className="rounded-full bg-green-100 px-2.5 py-1 text-xs text-green-800 dark:bg-green-900 dark:text-green-200"
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
              className="mt-2 rounded-md border-2 border-stone-800 py-3 font-medium text-stone-900 transition-colors hover:bg-stone-100 disabled:opacity-50 dark:border-stone-300 dark:text-stone-100 dark:hover:bg-stone-800"
              disabled={joining || code.trim().length === 0}
            >
              {joining ? "Joining…" : "Join room"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
