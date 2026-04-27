import { CSSProperties, ReactNode, useEffect, useMemo, useRef, useState } from "react";

type AdaptiveCardDeckProps = {
  children: ReactNode;
  itemCount: number;
  className?: string;
  gap?: number;
  idealCardWidth?: number;
  maxCardWidth?: number;
  maxColumns?: number;
  minCardWidth?: number;
  minColumns?: number;
};

type ColumnOptions = {
  gap: number;
  idealCardWidth: number;
  maxCardWidth: number;
  maxColumns: number;
  minCardWidth: number;
  minColumns: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function pickAdaptiveColumns(containerWidth: number, itemCount: number, options: ColumnOptions) {
  if (!Number.isFinite(containerWidth) || containerWidth <= 0) return 1;

  const safeItemCount = Math.max(1, itemCount);
  const cappedMaxColumns = clamp(options.maxColumns, options.minColumns, safeItemCount);
  const maxColumnsByWidth = Math.max(
    options.minColumns,
    Math.floor((containerWidth + options.gap) / (options.minCardWidth + options.gap)),
  );
  const limit = clamp(Math.min(cappedMaxColumns, maxColumnsByWidth), options.minColumns, safeItemCount);

  let bestColumns = options.minColumns;
  let bestScore = Number.POSITIVE_INFINITY;

  for (let columns = options.minColumns; columns <= limit; columns += 1) {
    const cardWidth = (containerWidth - options.gap * (columns - 1)) / columns;
    const underflow = Math.max(0, options.minCardWidth - cardWidth);
    const overflow = Math.max(0, cardWidth - options.maxCardWidth);
    const distanceToIdeal = Math.abs(cardWidth - options.idealCardWidth);
    const score = underflow * 6 + overflow * 3 + distanceToIdeal;

    if (score < bestScore || (score === bestScore && columns > bestColumns)) {
      bestScore = score;
      bestColumns = columns;
    }
  }

  return clamp(bestColumns, options.minColumns, safeItemCount);
}

export function AdaptiveCardDeck({
  children,
  itemCount,
  className,
  gap = 14,
  idealCardWidth = 360,
  maxCardWidth = 460,
  maxColumns = 4,
  minCardWidth = 280,
  minColumns = 1,
}: AdaptiveCardDeckProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [columns, setColumns] = useState(() =>
    clamp(Math.min(Math.max(1, itemCount), maxColumns), minColumns, Math.max(1, itemCount)),
  );

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;

    const measure = (width: number) => {
      const next = pickAdaptiveColumns(width, itemCount, {
        gap,
        idealCardWidth,
        maxCardWidth,
        maxColumns,
        minCardWidth,
        minColumns,
      });
      setColumns((current) => (current === next ? current : next));
    };

    measure(node.clientWidth);

    if (typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? node.clientWidth;
      measure(width);
    });

    observer.observe(node);
    return () => observer.disconnect();
  }, [gap, idealCardWidth, itemCount, maxCardWidth, maxColumns, minCardWidth, minColumns]);

  const style = useMemo(
    () =>
      ({
        "--adaptive-card-columns": `${Math.max(1, columns)}`,
        "--adaptive-card-gap": `${gap}px`,
      }) as CSSProperties,
    [columns, gap],
  );

  return (
    <div ref={containerRef} className={["adaptive-card-deck", className].filter(Boolean).join(" ")} style={style}>
      {children}
    </div>
  );
}
