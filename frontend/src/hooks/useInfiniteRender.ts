import { useEffect, useRef, useState } from "react";

/**
 * Render long lists incrementally ("Instagram-style"): show a first batch and
 * grow it as a sentinel scrolls into view, instead of mounting thousands of
 * rows at once. Returns the visible slice, a ref to attach to a sentinel element
 * placed at the end of the list, and whether more remain.
 *
 * Pass a `resetKey` that changes whenever the underlying list should restart
 * from the top (e.g. a new search term or a different playlist) — the visible
 * count snaps back to one batch.
 */
export function useInfiniteRender<T>(
  items: T[],
  { step = 40, resetKey = "" }: { step?: number; resetKey?: unknown } = {},
) {
  const [count, setCount] = useState(step);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // Restart from the top when the list identity changes (search, tab, etc.).
  useEffect(() => {
    setCount(step);
  }, [resetKey, step]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setCount((c) => (c < items.length ? c + step : c));
        }
      },
      // Pre-load a little before the sentinel is actually visible.
      { rootMargin: "600px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [items.length, step]);

  const visible = items.slice(0, count);
  const hasMore = count < items.length;
  return { visible, hasMore, sentinelRef };
}
