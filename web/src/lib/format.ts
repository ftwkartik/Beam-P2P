/** Human-readable byte sizes and durations for transfer progress UI. */

const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < UNITS.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${UNITS[unitIndex]}`;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "--";
  if (seconds < 1) return "<1s";
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins === 0) return `${secs}s`;
  const hours = Math.floor(mins / 60);
  if (hours === 0) return `${mins}m ${secs}s`;
  return `${hours}h ${mins % 60}m`;
}
