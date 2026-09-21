import { useEffect, useState } from "react";
import QRCode from "qrcode";

interface QrCodeProps {
  value: string;
  size?: number;
}

/** Renders `value` (the share link) as a QR code image (docs/roadmap.md Milestone 7). */
export function QrCode({ value, size = 180 }: QrCodeProps) {
  const [dataUrl, setDataUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    QRCode.toDataURL(value, { width: size, margin: 1, color: { dark: "#14532d", light: "#f6efd8" } })
      .then((url) => {
        if (!cancelled) setDataUrl(url);
      })
      .catch((err: unknown) => {
        console.error("beam: failed to render QR code", err);
      });
    return () => {
      cancelled = true;
    };
  }, [value, size]);

  if (!dataUrl) {
    return (
      <div
        className="animate-pulse rounded-md border-2 border-stone-800 bg-stone-200 dark:border-stone-300 dark:bg-stone-800"
        style={{ width: size, height: size }}
        aria-hidden="true"
      />
    );
  }

  return (
    <img
      src={dataUrl}
      alt="QR code for the room share link"
      width={size}
      height={size}
      className="rounded-md border-2 border-stone-800 dark:border-stone-300"
    />
  );
}
