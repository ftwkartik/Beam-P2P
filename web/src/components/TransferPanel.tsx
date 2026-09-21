import { useRef, useState } from "react";

import { formatBytes, formatDuration } from "../lib/format";
import { useSessionStore } from "../state/session-store";

function ProgressBar({ fraction }: { fraction: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800">
      <div
        className="h-full rounded-full bg-green-600 transition-[width]"
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
    <section className="mt-4 rounded-md border-2 border-stone-800 p-4 dark:border-stone-300">
      <p className="text-sm font-medium text-stone-900 dark:text-stone-100">
        Sending {progress ? `(${progress.fileIndex + 1}/${progress.fileCount})` : ""}
      </p>
      {progress && (
        <>
          <div className="mt-2">
            <ProgressBar fraction={progress.totalBytesSent / Math.max(1, progress.totalBytes)} />
          </div>
          <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">
            {formatBytes(progress.totalBytesSent)} / {formatBytes(progress.totalBytes)}
            {rate && rate.bytesPerSecond > 0 &&
              ` -- ${formatBytes(rate.bytesPerSecond)}/s -- ${formatDuration(rate.etaSeconds)} left`}
          </p>
        </>
      )}
      {outgoing.phase === "completed" && <p className="mt-1 text-sm text-green-700 dark:text-green-400">Done.</p>}
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
    <section className="mt-4 rounded-md border-2 border-green-800 bg-green-50 p-4 dark:border-green-400 dark:bg-green-950">
      <p className="text-sm font-medium text-green-900 dark:text-green-200">
        The other device wants to send you {offer.files.length} file{offer.files.length === 1 ? "" : "s"} (
        {formatBytes(totalSize)})
      </p>
      <ul className="mt-2 max-h-32 overflow-y-auto text-sm text-stone-600 dark:text-stone-400">
        {offer.files.map((f) => (
          <li key={f.index} className="truncate">
            {f.path} -- {formatBytes(f.size)}
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className="rounded-md border-2 border-green-900 bg-green-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-green-700 dark:border-green-400"
          onClick={accept}
        >
          Accept
        </button>
        <button
          type="button"
          className="rounded-md border-2 border-stone-400 px-4 py-2 text-sm text-stone-900 dark:border-stone-600 dark:text-stone-100"
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
    <section className="mt-4 rounded-md border-2 border-stone-800 p-4 dark:border-stone-300">
      <p className="text-sm font-medium text-stone-900 dark:text-stone-100">
        Receiving {progress ? `(${progress.fileIndex + 1}/${progress.fileCount})` : ""}
      </p>
      {progress && (
        <>
          <div className="mt-2">
            <ProgressBar fraction={progress.totalBytesVerified / Math.max(1, progress.totalBytes)} />
          </div>
          <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">
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
    <section className="mt-4 rounded-md border-2 border-green-800 bg-green-50 p-4 dark:border-green-400 dark:bg-green-950">
      <p className="text-sm font-medium text-green-900 dark:text-green-300">Received and verified</p>
      <ul className="mt-2 space-y-1">
        {readyFiles.map((f) => {
          const url = URL.createObjectURL(f.blob);
          return (
            <li key={f.fileIndex}>
              <a href={url} download={f.offer.path.split("/").pop()} className="text-sm text-green-700 underline dark:text-green-300">
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
      <section className="mt-4 flex gap-2">
        <button
          type="button"
          className="flex-1 rounded-md border-2 border-stone-800 py-3 text-sm font-medium text-stone-900 transition-colors hover:bg-stone-100 dark:border-stone-300 dark:text-stone-100 dark:hover:bg-stone-800"
          onClick={() => fileInputRef.current?.click()}
        >
          Send files
        </button>
        <button
          type="button"
          className="flex-1 rounded-md border-2 border-stone-800 py-3 text-sm font-medium text-stone-900 transition-colors hover:bg-stone-100 dark:border-stone-300 dark:text-stone-100 dark:hover:bg-stone-800"
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
