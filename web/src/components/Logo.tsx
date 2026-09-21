interface LogoProps {
  size?: "sm" | "lg";
  /** Renders the wordmark as the page's `<h1>` (App.test.tsx and screen readers both
   * expect exactly one "Beam" heading per page) instead of a plain `<div>`. */
  heading?: boolean;
}

/** A small blocky "two devices, one link" mark plus the pixel-font wordmark, built
 * from plain rects (crisp at any size, no image asset to ship) rather than a font
 * icon or SVG path library the project doesn't otherwise depend on. */
function LogoMark({ pixel }: { pixel: number }) {
  const s = pixel;
  return (
    <svg
      width={s * 9}
      height={s * 7}
      viewBox={`0 0 9 7`}
      shapeRendering="crispEdges"
      aria-hidden="true"
      className="shrink-0"
    >
      <rect x="0" y="0" width="3" height="3" className="fill-green-700 dark:fill-green-500" />
      <rect x="1" y="1" width="1" height="1" className="fill-parchment dark:fill-parchment-dark" />
      <rect x="6" y="4" width="3" height="3" className="fill-green-700 dark:fill-green-500" />
      <rect x="7" y="5" width="1" height="1" className="fill-parchment dark:fill-parchment-dark" />
      <rect x="3" y="3" width="1" height="1" className="fill-green-500 dark:fill-green-400" />
      <rect x="4" y="2" width="1" height="1" className="fill-green-500 dark:fill-green-400" />
      <rect x="5" y="3" width="1" height="1" className="fill-green-500 dark:fill-green-400" />
    </svg>
  );
}

/** The "Beam" wordmark, used on both pages so the header stays identical. */
export function Logo({ size = "lg", heading = false }: LogoProps) {
  const isLarge = size === "lg";
  const Wrapper = heading ? "h1" : "div";
  return (
    <Wrapper className={`flex items-center justify-center ${isLarge ? "gap-3" : "gap-2"}`}>
      <LogoMark pixel={isLarge ? 5 : 4} />
      <span
        className={`text-green-800 dark:text-green-400 ${isLarge ? "text-xl" : "text-base"}`}
        style={{ fontFamily: "var(--font-pixel)" }}
      >
        Beam
      </span>
    </Wrapper>
  );
}
