/**
 * Short authentication string (SAS) derivation -- a TypeScript port of
 * beam_protocol.sas.derive_sas (docs/protocol.md §4.4, docs/adr/009-sas-verification.md).
 * Must stay byte-for-byte consistent with the Python implementation; pinned by the
 * shared golden vectors in tests/vectors/sas.json (see sas.test.ts).
 *
 * Uses the Web Crypto API (`crypto.subtle.digest`), available in every browser in a
 * secure context (https, or localhost during development).
 */

import { SAS_TABLE } from "./generated/sas-table";

const SAS_DOMAIN = "beam-sas-v1";
const SAS_SYMBOL_COUNT = 5;

if (SAS_TABLE.length !== 256) {
  throw new Error("the SAS table must have exactly 256 entries (8 bits/symbol)");
}

const FINGERPRINT_RE = /^sha-256 ([0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){31})$/;

export class InvalidFingerprintError extends Error {}

export interface SasSymbol {
  emoji: string;
  name: string;
}

/** Validates and uppercases a `sha-256 AB:CD:...` DTLS fingerprint from an SDP. */
export function normalizeFingerprint(fingerprint: string): string {
  const candidate = fingerprint.trim();
  if (!FINGERPRINT_RE.test(candidate)) {
    throw new InvalidFingerprintError(
      "expected a 'sha-256 AB:CD:...' fingerprint with 32 hex octets",
    );
  }
  const [scheme, hexPart] = candidate.split(" ", 2);
  return `${scheme} ${hexPart.toUpperCase()}`;
}

/**
 * Derives the 5-symbol SAS for a room from both sides' DTLS fingerprints.
 * `fingerprintA`/`fingerprintB` may be passed in either order: they are sorted before
 * hashing so both peers (who each know "mine" and "theirs", not "A" and "B") compute
 * the identical input and therefore the identical SAS.
 */
export async function deriveSas(
  roomId: string,
  fingerprintA: string,
  fingerprintB: string,
): Promise<SasSymbol[]> {
  const fpA = normalizeFingerprint(fingerprintA);
  const fpB = normalizeFingerprint(fingerprintB);
  const [first, second] = [fpA, fpB].sort();

  const encoder = new TextEncoder();
  const message = new Uint8Array([
    ...encoder.encode(SAS_DOMAIN),
    ...encoder.encode(roomId),
    ...encoder.encode(first),
    ...encoder.encode(second),
  ]);

  const digestBuffer = await crypto.subtle.digest("SHA-256", message);
  const digest = new Uint8Array(digestBuffer);

  const symbols: SasSymbol[] = [];
  for (let i = 0; i < SAS_SYMBOL_COUNT; i++) {
    const [emoji, name] = SAS_TABLE[digest[i]];
    symbols.push({ emoji, name });
  }
  return symbols;
}

/** Renders a SAS as a single space-separated line for display or logging. */
export function formatSas(symbols: readonly SasSymbol[]): string {
  return symbols.map((s) => `${s.emoji} ${s.name}`).join("  ");
}
