/**
 * Manifest limits and path safety -- a TypeScript port of the validation in
 * beam_protocol.peer (docs/security.md §4, "Peer-to-peer protocol safety"). The
 * receiver re-validates every path here too: a sender is not assumed to be honest just
 * because it's the other end of an already-authenticated WebRTC connection.
 */

import type { FileOffer } from "../../protocol/generated/peer-message";

export const MAX_FILES_PER_TRANSFER = 10_000;
export const MAX_PATH_SEGMENT_BYTES = 255;
export const MAX_PATH_TOTAL_BYTES = 4096;
/** Number.MAX_SAFE_INTEGER: sizes above this can't round-trip through JSON exactly. */
export const MAX_FILE_SIZE = 2 ** 53 - 1;
export const DEFAULT_BLOCK_SIZE = 1024 * 1024;

const WINDOWS_RESERVED_NAMES = new Set([
  "CON",
  "PRN",
  "AUX",
  "NUL",
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
]);

// eslint-disable-next-line no-control-regex -- deliberately matching control chars (path safety)
const CONTROL_CHAR_RE = /[\x00-\x1f]/;
const encoder = new TextEncoder();

export class PathSafetyError extends Error {}

/** Validates a manifest entry's path is a safe, relative, forward-slash path. */
export function validateRelativePath(path: string): string {
  if (!path || path !== path.trim()) {
    throw new PathSafetyError("path must be non-empty with no leading/trailing whitespace");
  }
  if (encoder.encode(path).length > MAX_PATH_TOTAL_BYTES) {
    throw new PathSafetyError(`path exceeds ${MAX_PATH_TOTAL_BYTES} bytes`);
  }
  if (path.includes("\\")) {
    throw new PathSafetyError("path must use forward slashes");
  }
  if (path.startsWith("/") || path.startsWith("~")) {
    throw new PathSafetyError("path must be relative");
  }
  if (CONTROL_CHAR_RE.test(path)) {
    throw new PathSafetyError("path contains control characters");
  }
  if (path.length >= 2 && path[1] === ":") {
    throw new PathSafetyError("path must not contain a drive letter");
  }

  for (const segment of path.split("/")) {
    if (segment === "" || segment === "." || segment === "..") {
      throw new PathSafetyError("path must not contain empty, '.' or '..' segments");
    }
    if (encoder.encode(segment).length > MAX_PATH_SEGMENT_BYTES) {
      throw new PathSafetyError(`path segment exceeds ${MAX_PATH_SEGMENT_BYTES} bytes`);
    }
    const bareName = segment.split(".")[0].toUpperCase();
    if (WINDOWS_RESERVED_NAMES.has(bareName)) {
      throw new PathSafetyError(`path segment '${segment}' is a reserved name on Windows`);
    }
  }

  return path;
}

export class ManifestError extends Error {}

/** Builds and validates the `offer_files` manifest entries for a set of browser Files. */
export function buildManifest(files: readonly File[]): FileOffer[] {
  if (files.length === 0) {
    throw new ManifestError("a transfer must offer at least one file");
  }
  if (files.length > MAX_FILES_PER_TRANSFER) {
    throw new ManifestError(`cannot offer more than ${MAX_FILES_PER_TRANSFER} files at once`);
  }

  const offers = files.map((file, index) => {
    if (file.size > MAX_FILE_SIZE) {
      throw new ManifestError(`${file.name} is too large to transfer`);
    }
    return {
      index,
      path: validateRelativePath(relativePathOf(file)),
      size: file.size,
      mime: file.type || "application/octet-stream",
      mtime: Number.isFinite(file.lastModified) ? file.lastModified : null,
    };
  });

  const paths = new Set(offers.map((f) => f.path));
  if (paths.size !== offers.length) {
    throw new ManifestError("file paths must be unique within a transfer");
  }

  return offers;
}

/** A dropped/selected directory's files carry their relative path in `webkitRelativePath`. */
function relativePathOf(file: File): string {
  const withRelativePath = file as File & { webkitRelativePath?: string };
  return withRelativePath.webkitRelativePath || file.name;
}
