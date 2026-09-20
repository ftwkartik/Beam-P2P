import { execFileSync, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** The workspace root -- `uv run` needs to run from here to see the `beam-cli`
 * package (`pyproject.toml`'s `[tool.uv.workspace]`), not from `web/`. */
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

/** A running `beam` CLI subprocess (interop.spec.ts's counterpart to a Playwright
 * `Page`): the browser side of these scenarios drives a real page, this drives a real
 * `beam send`/`beam receive` process against the same compose stack, over the same
 * real WebRTC connection. Output is line-buffered so a test can wait for a specific
 * line (the room code, a progress message) while the process is still running,
 * mirroring how the browser fixtures wait for specific text to render. */
export class CliProcess {
  readonly proc: ChildProcessWithoutNullStreams;
  private readonly lines: string[] = [];
  private waiters: { pattern: RegExp; resolve: (line: string) => void }[] = [];

  constructor(args: string[]) {
    this.proc = spawn("uv", ["run", "--package", "beam-cli", "beam", ...args], {
      cwd: REPO_ROOT,
      // Rich (ui.py) checks FORCE_COLOR before it ever checks isatty() -- npx/
      // Playwright set it for their own colored output, and inheriting it here
      // tricks Rich into treating this pipe as a real terminal, so it live-redraws
      // the (empty, task-less) progress display forever with ANSI clear-line codes
      // while the CLI just sits there waiting for a peer, instead of printing plain
      // lines. Force it back off so stdout is the plain text these tests parse.
      env: { ...process.env, FORCE_COLOR: "", NO_COLOR: "1" },
    });
    createInterface({ input: this.proc.stdout }).on("line", (line) => this.handleLine(line));
    createInterface({ input: this.proc.stderr }).on("line", (line) => this.handleLine(line));
  }

  private handleLine(line: string): void {
    this.lines.push(line);
    const matched = this.waiters.filter((w) => w.pattern.test(line));
    if (matched.length === 0) return;
    this.waiters = this.waiters.filter((w) => !matched.includes(w));
    for (const waiter of matched) waiter.resolve(line);
  }

  /** Resolves with the first line (already seen or yet to arrive) matching
   * `pattern`. Rejects with the CLI's full output so far if none arrives in time. */
  waitForLine(pattern: RegExp, timeoutMs = 30_000): Promise<string> {
    const already = this.lines.find((l) => pattern.test(l));
    if (already) return Promise.resolve(already);
    return new Promise((resolve, reject) => {
      const waiter = {
        pattern,
        resolve: (line: string) => {
          clearTimeout(timer);
          resolve(line);
        },
      };
      const timer = setTimeout(() => {
        this.waiters = this.waiters.filter((w) => w !== waiter);
        reject(new Error(`timed out waiting for ${pattern} in CLI output:\n${this.lines.join("\n")}`));
      }, timeoutMs);
      this.waiters.push(waiter);
    });
  }

  waitForExit(timeoutMs = 60_000): Promise<number> {
    if (this.proc.exitCode !== null) return Promise.resolve(this.proc.exitCode);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.kill();
        reject(new Error(`CLI process did not exit in time:\n${this.lines.join("\n")}`));
      }, timeoutMs);
      this.proc.once("exit", (code) => {
        clearTimeout(timer);
        resolve(code ?? -1);
      });
    });
  }

  /** Cleanup for when a test fails before the process would exit on its own.
   * `uv run` forks the actual `beam` process as its own child in its OWN process
   * group (confirmed by observation: putting the `uv` process itself in a `detached`
   * group and signaling that group's negative pid still left `beam` running) --
   * killing only `this.proc` leaves that grandchild orphaned, holding a real
   * RTCPeerConnection, ICE candidates and an open signaling WebSocket. `pkill -P`
   * reaches it directly by parent pid instead of relying on group membership. */
  kill(): void {
    if (this.proc.exitCode !== null || this.proc.pid === undefined) return;
    try {
      execFileSync("pkill", ["-9", "-P", String(this.proc.pid)]);
    } catch {
      // pkill exits non-zero when nothing matched (e.g. beam hadn't forked yet) --
      // not an error condition here.
    }
    this.proc.kill("SIGKILL");
  }
}

/** Starts `beam send <path> --server <baseUrl>` and returns once it has printed the
 * room code, without waiting for a peer to join. */
export async function cliSend(filePath: string, baseUrl: string): Promise<{ cli: CliProcess; code: string }> {
  const cli = new CliProcess(["send", filePath, "--server", baseUrl]);
  const line = await cli.waitForLine(/^Room code: (\S+)/);
  const match = /^Room code: (\S+)/.exec(line);
  if (!match) throw new Error(`unexpected room-code line: ${line}`);
  return { cli, code: match[1] };
}

/** Starts `beam receive <code> --dir <dir> --yes --server <baseUrl>`, auto-accepting
 * (no interactive consent prompt -- these scenarios test the transfer, not the CLI's
 * own prompt UI, which isn't Playwright's concern). */
export function cliReceive(code: string, dir: string, baseUrl: string): CliProcess {
  return new CliProcess(["receive", code, "--dir", dir, "--yes", "--server", baseUrl]);
}
