/**
 * Room code parsing and normalization -- a TypeScript port of
 * beam_protocol.codes.normalize_code (docs/protocol.md §1). Must stay byte-for-byte
 * consistent with the Python implementation; pinned by the shared golden vectors in
 * tests/vectors/codes.json (see codes.test.ts).
 */

import { WORDLIST } from "./generated/wordlist";

const WORDS_PER_CODE = 3;
const MIN_NAMEPLATE = 1;
const MAX_NAMEPLATE = 9999;

const WORDLIST_SET = new Set(WORDLIST);

export class InvalidCodeError extends Error {}

export interface RoomCode {
  nameplate: number;
  words: readonly [string, string, string];
}

function fold(text: string): string {
  return text.normalize("NFKC").trim().toLowerCase();
}

/** Formats a canonical `nameplate-word-word-word` code, e.g. "7-otter-lantern-tiger". */
export function formatCode(nameplate: number, words: readonly string[]): string {
  if (nameplate < MIN_NAMEPLATE || nameplate > MAX_NAMEPLATE) {
    throw new RangeError(`nameplate ${nameplate} is outside [${MIN_NAMEPLATE}, ${MAX_NAMEPLATE}]`);
  }
  return [String(nameplate), ...words].join("-");
}

/**
 * Parses and validates user-typed input into a canonical `RoomCode`. Accepts variable
 * whitespace and separators between tokens, since a code is often read aloud or pasted
 * with different formatting. Never reveals which word (if any) was wrong -- see
 * docs/security.md §1 on not distinguishing "unknown room" from "wrong code".
 */
export function normalizeCode(raw: string): RoomCode {
  const folded = fold(raw);
  const tokens = folded.replaceAll("-", " ").split(" ").filter(Boolean);

  if (tokens.length !== 1 + WORDS_PER_CODE) {
    throw new InvalidCodeError("A room code has a number followed by three words.");
  }

  const [nameplateToken, ...wordTokens] = tokens;
  if (!/^\d+$/.test(nameplateToken)) {
    throw new InvalidCodeError("A room code starts with a number.");
  }

  const nameplate = Number.parseInt(nameplateToken, 10);
  if (nameplate < MIN_NAMEPLATE || nameplate > MAX_NAMEPLATE) {
    throw new InvalidCodeError("That room code's number looks wrong.");
  }

  for (const word of wordTokens) {
    if (!WORDLIST_SET.has(word)) {
      throw new InvalidCodeError("That room code's words look wrong.");
    }
  }

  const words = wordTokens as [string, string, string];
  return { nameplate, words };
}

export function roomCodeToString(code: RoomCode): string {
  return formatCode(code.nameplate, code.words);
}

/** Up to `limit` wordlist entries starting with `partial`, for input autocompletion. */
export function suggestCompletions(partial: string, limit = 8): string[] {
  const prefix = fold(partial);
  if (!prefix) return [];
  const matches: string[] = [];
  for (const word of WORDLIST) {
    if (word.startsWith(prefix)) {
      matches.push(word);
      if (matches.length >= limit) break;
    }
  }
  return matches;
}
