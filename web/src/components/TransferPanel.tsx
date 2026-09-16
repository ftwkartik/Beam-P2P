import { useRef, useState } from "react";

import { formatBytes, formatDuration } from "../lib/format";
import { useSessionStore } from "../state/session-store";

function ProgressBar({ fraction }: { fraction: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
      <div
        className="h-full rounded-full bg-indigo-600 transition-[width]"
        style={{ width: `${Math.min(100, Math.max(0, fraction * 100))}%` }}
      />
    </div>
  );
}

function OutgoingTransfer() {
  const outgoing = useSessionStore((s) => s.outgoingTransfer);
  if (!outgoing) return null;
  const progress = outgoing.progress;
  const rate = outgoing.rate;

  return (
    <section className="mt-6 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <p className="text-sm font-medium">Sending {progress ? `(${progress.fileIndex + 1}/${progress.fileCount})` : ""}</p>
      {progress && (
        <>
          <div className="mt-2">
            <ProgressBar fraction={progress.totalBytesSent / Math.max(1, progress.totalBytes)} />
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {formatBytes(progress.totalBytesSent)} / {formatBytes(progress.totalBytes)}
            {rate && rate.bytesPerSecond > 0 &&
              ` -- ${formatBytes(rate.bytesPerSecond)}/s -- ${formatDuration(rate.etaSeconds)} left`}
          </p>
        </>
      )}
      {outgoing.phase === "completed" && <p className="mt-1 text-sm text-emerald-700 dark:text-emerald-400">Done.</p>}
      {outgoing.error && <p className="mt-1 text-sm text-red-600 dark:text-red-400">{outgoing.error}</p>}
    </section>
  );
}

function IncomingOfferConsent() {
  const offer = useSessionStore((s) => s.incomingOffer);
  const accept = useSessionStore((s) => s.acceptIncomingTransfer);
  const decline = useSessionStore((s) => s.declineIncomingTransfer);

  if (!offer) return null;
  const totalSize = offer.files.reduce((sum, f) => sum + f.size, 0);

  return (
    <section className="mt-6 rounded-xl border border-indigo-300 bg-indigo-50 p-4 dark:border-indigo-800 dark:bg-indigo-950">
      <p className="text-sm font-medium text-indigo-900 dark:text-indigo-200">
        The other device wants to send you {offer.files.length} file{offer.files.length === 1 ? "" : "s"} (
        {formatBytes(totalSize)})
      </p>
      <ul className="mt-2 max-h-32 overflow-y-auto text-sm text-slate-600 dark:text-slate-400">
        {offer.files.map((f) => (
          <li key={f.index} className="truncate">
            {f.path} -- {formatBytes(f.size)}
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white"
          onClick={accept}
        >
          Accept
        </button>
        <button
          type="button"
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm dark:border-slate-700"
          onClick={() => decline()}
        >
          Decline
        </button>
      </div>
    </section>
  );
}

function IncomingTransfer() {
  const incoming = useSessionStore((s) => s.incomingTransfer);
  if (!incoming) return null;
  const progress = incoming.progress;
  const rate = incoming.rate;

  return (
    <section className="mt-6 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <p className="text-sm font-medium">Receiving {progress ? `(${progress.fileIndex + 1}/${progress.fileCount})` : ""}</p>
      {progress && (
        <>
          <div className="mt-2">
            <ProgressBar fraction={progress.totalBytesVerified / Math.max(1, progress.totalBytes)} />
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {formatBytes(progress.totalBytesVerified)} / {formatBytes(progress.totalBytes)}
            {rate && rate.bytesPerSecond > 0 &&
              ` -- ${formatBytes(rate.bytesPerSecond)}/s -- ${formatDuration(rate.etaSeconds)} left`}
          </p>
        </>
      )}
      {incoming.error && <p className="mt-1 text-sm text-red-600 dark:text-red-400">{incoming.error}</p>}
    </section>
  );
}

function ReadyFiles() {
  const readyFiles = useSessionStore((s) => s.readyFiles);
  if (readyFiles.length === 0) return null;

  return (
    <section className="mt-6 rounded-xl border border-emerald-300 bg-emerald-50 p-4 dark:border-emerald-800 dark:bg-emerald-950">
      <p className="text-sm font-medium text-emerald-800 dark:text-emerald-300">Received and verified</p>
      <ul className="mt-2 space-y-1">
        {readyFiles.map((f) => {
          const url = URL.createObjectURL(f.blob);
          return (
            <li key={f.fileIndex}>
              <a href={url} download={f.offer.path.split("/").pop()} className="text-sm text-indigo-700 underline dark:text-indigo-300">
                Save {f.offer.path} ({formatBytes(f.offer.size)})
              </a>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** File/folder pickers plus every transfer-related panel; shown once connected. */
export function TransferPanel() {
  const sendFiles = useSessionStore((s) => s.sendFiles);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [pickError, setPickError] = useState<string | null>(null);

  function handleFiles(fileList: FileList | null): void {
    if (!fileList || fileList.length === 0) return;
    setPickError(null);
    try {
      sendFiles(Array.from(fileList));
    } catch (err) {
      setPickError(err instanceof Error ? err.message : "Could not start that transfer.");
    }
  }

  return (
    <>
      <section className="mt-6 flex gap-2">
        <button
          type="button"
          className="flex-1 rounded-lg border border-slate-300 py-3 text-sm font-medium dark:border-slate-700"
          onClick={() => fileInputRef.current?.click()}
        >
          Send files
        </button>
        <button
          type="button"
          className="flex-1 rounded-lg border border-slate-300 py-3 text-sm font-medium dark:border-slate-700"
          onClick={() => folderInputRef.current?.click()}
        >
          Send a folder
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          data-testid="file-input"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error -- webkitdirectory has no TS DOM typing, but every major browser supports it.
          webkitdirectory=""
          className="hidden"
          data-testid="folder-input"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </section>
      {pickError && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{pickError}</p>}

      <IncomingOfferConsent />
      <OutgoingTransfer />
      <IncomingTransfer />
      <ReadyFiles />
    </>
  );
}
