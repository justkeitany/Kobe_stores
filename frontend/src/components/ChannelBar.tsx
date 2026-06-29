import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, Loader2, LayoutGrid, Tv, Crown } from "lucide-react";
import api, { mintStreamToken } from "../lib/api";

interface Channel {
  key: string;
  stream_id: number | null;
  name: string;
  logo: string;
  source: string;        // category / playlist name — used as the box subtitle
  imported: boolean;
  is_enabled: boolean;
  health: "online" | "offline" | "geo" | "dead" | "checking";
  url?: string;
}

// Watchable first (online, then still-checking, then geo, offline, dead) so the
// channels a viewer can actually open lead each group.
const HEALTH_ORDER: Record<string, number> = {
  online: 0, checking: 1, geo: 2, offline: 3, dead: 4,
};

const playable = (arr: Channel[]) =>
  arr.filter((c) => (c.imported && c.stream_id != null) || !!c.url);

const byHealth = (arr: Channel[]) =>
  arr
    .map((c, i) => ({ c, i }))
    .sort((a, b) => (HEALTH_ORDER[a.c.health] ?? 9) - (HEALTH_ORDER[b.c.health] ?? 9) || a.i - b.i)
    .map((x) => x.c);

// How many boxes to render in the first paint, and how many to add per tick as
// the rest stream in. Keeping the first paint small is what makes the popup open
// instantly and keeps the video behind it from stuttering.
const FIRST_BATCH = 15;
const NEXT_BATCH = 24;

/**
 * In-player channel switcher. Mirrors the EPG strip: pops up over the bottom
 * quarter while the video keeps playing behind it, and the user swipes/scrolls
 * left-right through every channel. Tapping a box switches the player to that
 * channel in place — no going back to the channels page.
 *
 * Premium channels are kept in their own labelled group (gold) at the front of
 * the strip, separated from the regular channels — the general /api/channels
 * directory already excludes premium, so we pull them from /api/premium/channels.
 *
 * To stay fast with hundreds of channels the strip renders progressively: a
 * small first batch (covering the current channel + its neighbours) paints
 * immediately, then the rest trickle in on a timer instead of all at once.
 */
export default function ChannelBar({
  currentStreamId,
  onPick,
  onClose,
}: {
  currentStreamId: number;          // NaN when the current stream isn't imported
  onPick: (token: string, name: string, sid: number | null) => void;
  onClose: () => void;
}) {
  const [switching, setSwitching] = useState<string | null>(null); // key being opened
  const [count, setCount] = useState(FIRST_BATCH); // how many boxes are revealed
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrolledRef = useRef(false);

  // Shared react-query caches: ["all-channels"] is the same key the Channels page
  // uses, so coming from there the data is already warm and the strip opens
  // instantly. staleTime keeps reopens instant without hammering the API.
  const { data: others = [], isLoading: l1 } = useQuery<Channel[]>({
    queryKey: ["all-channels"],
    queryFn: () => api.get<Channel[]>("/channels").then((r) => r.data),
    staleTime: 60_000,
  });
  const { data: premium = [], isLoading: l2 } = useQuery<Channel[]>({
    queryKey: ["premium-channels"],
    queryFn: () => api.get<Channel[]>("/premium/channels").then((r) => r.data),
    staleTime: 60_000,
  });

  // Flatten the two groups into one ordered sequence (premium first), tagging
  // each entry with its group + whether it's the first of that group (so we know
  // where to drop the group label). One flat list makes progressive slicing and
  // "scroll to current" trivial.
  const premItems = byHealth(playable(premium));
  const allItems = byHealth(playable(others));
  const flat: { c: Channel; premium: boolean; groupStart: boolean }[] = [
    ...premItems.map((c, i) => ({ c, premium: true, groupStart: i === 0 })),
    ...allItems.map((c, i) => ({ c, premium: false, groupStart: i === 0 })),
  ];
  const total = flat.length;
  const loading = total === 0 && (l1 || l2);

  const currentIndex = Number.isNaN(currentStreamId)
    ? -1
    : flat.findIndex((e) => e.c.stream_id === currentStreamId);

  // Always render through the current channel (+ a buffer) on the first paint so
  // it's present for the scroll-to-current below, even if it sorts deep.
  const renderCount = Math.min(
    total,
    Math.max(count, currentIndex >= 0 ? currentIndex + FIRST_BATCH : 0),
  );
  const shown = flat.slice(0, renderCount);

  // Trickle in the remaining boxes after the first paint — "load what's needed
  // now, then the rest slowly" — so we never block on rendering the whole list.
  useEffect(() => {
    if (count >= total) return;
    const id = window.setTimeout(() => setCount((c) => Math.min(total, c + NEXT_BATCH)), 90);
    return () => window.clearTimeout(id);
  }, [count, total]);

  // Open the strip scrolled to the channel that's playing now (left edge), once,
  // so the boxes to its right are the "next" channels. Instant (no animation) and
  // only on first open — switching channels later won't yank the strip around.
  useLayoutEffect(() => {
    if (scrolledRef.current || loading || !scrollRef.current || currentIndex < 0) return;
    const el = scrollRef.current.querySelector<HTMLElement>('[data-current="1"]');
    if (el) {
      el.scrollIntoView({ behavior: "instant" as ScrollBehavior, inline: "start", block: "nearest" });
      scrolledRef.current = true;
    }
  }, [loading, total, currentIndex]);

  async function pick(c: Channel) {
    if (switching) return;
    setSwitching(c.key);
    try {
      const token =
        c.imported && c.stream_id != null
          ? await mintStreamToken({ stream_id: c.stream_id })
          : c.url
          ? await mintStreamToken({ url: c.url })
          : null;
      // Hand off to the player. It keeps this popup open (spinner stays on this
      // box) until the new channel actually starts playing, then closes it.
      if (token) onPick(token, c.name, c.imported ? c.stream_id : null);
      else setSwitching(null);
    } catch {
      setSwitching(null);
    }
  }

  function box(c: Channel, isPremium: boolean) {
    const isCurrent = !Number.isNaN(currentStreamId) && c.stream_id === currentStreamId;
    const isSwitching = switching === c.key;
    return (
      <button
        key={c.key}
        data-current={isCurrent ? "1" : undefined}
        onClick={() => pick(c)}
        // Exactly 5 boxes fit the row (matches the EPG strip); scroll for the
        // rest. Everything inside is centered.
        style={{ flex: "0 0 auto", width: "calc((100% - 2rem) / 5)" }}
        className={`relative rounded-lg px-3 py-2 flex flex-col items-center justify-center text-center
          gap-1.5 overflow-hidden border backdrop-blur-sm transition-colors
          ${isCurrent
            ? "bg-red-600/30 border-red-500/70"
            : isPremium
            ? "bg-black/40 border-[#f5c86e]/35 hover:bg-black/55 hover:border-[#f5c86e]/60"
            : "bg-black/40 border-white/15 hover:bg-black/55 hover:border-white/30"}`}
      >
        {/* Logo (or a fallback TV glyph), centered. Lazy + async so off-screen
            logos don't all decode at once and stall the video. */}
        <div className="h-10 flex items-center justify-center shrink-0">
          {c.logo ? (
            <img
              src={c.logo}
              alt=""
              loading="lazy"
              decoding="async"
              className="max-h-10 max-w-[72px] object-contain"
              onError={(e) => { e.currentTarget.style.display = "none"; }}
            />
          ) : (
            <Tv size={26} className="text-white/40" />
          )}
        </div>

        {/* Name, centered. */}
        <div className="text-white text-[13px] font-semibold leading-tight line-clamp-2 shrink-0">
          {c.name}
        </div>

        {/* Description / source line, centered, if available. */}
        {c.source && (
          <div className="text-white/50 text-[11px] leading-snug line-clamp-1 shrink-0">
            {c.source}
          </div>
        )}

        {isPremium && !isCurrent && (
          <Crown size={12} className="absolute top-1.5 left-1.5 text-[#f5c86e]" />
        )}
        {isCurrent && (
          <span className="absolute top-1.5 right-1.5 text-[10px] font-bold tracking-wide text-red-400">
            ● NOW
          </span>
        )}
        {isSwitching && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50">
            <Loader2 size={20} className="animate-spin text-white" />
          </div>
        )}
      </button>
    );
  }

  function groupLabel(isPremium: boolean) {
    return (
      <div
        key={isPremium ? "lbl-premium" : "lbl-all"}
        style={{ flex: "0 0 auto" }}
        className="self-stretch flex items-center pr-1"
      >
        <div className={`flex flex-col items-center gap-1.5 ${isPremium ? "text-[#f5c86e]" : "text-white/45"}`}>
          {isPremium ? <Crown size={16} /> : <LayoutGrid size={15} />}
          <span
            style={{ writingMode: "vertical-rl" }}
            className="rotate-180 text-[10px] font-bold uppercase tracking-[0.15em] whitespace-nowrap"
          >
            {isPremium ? "Premium" : "All Channels"}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="absolute bottom-0 left-0 right-0 z-30 h-1/4 min-h-[190px] flex flex-col"
      // Don't let taps/scrolls here toggle the video play state behind it — the
      // video keeps playing in the background while the switcher is open.
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header — no background panel; a drop-shadow keeps it legible over video. */}
      <div className="flex items-center gap-2 px-4 pt-2.5 pb-1.5 shrink-0 drop-shadow-[0_1px_3px_rgba(0,0,0,0.9)]">
        <LayoutGrid size={15} className="text-red-500" />
        <span className="text-white text-sm font-semibold truncate">Channels</span>
        <span className="text-white/50 text-xs">· swipe to switch</span>
        <div className="flex-1" />
        <button
          onClick={onClose}
          className="text-white/70 hover:text-white p-1 -mr-1 transition-colors"
          title="Close channels"
        >
          <X size={18} />
        </button>
      </div>

      {/* Strip */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center text-white/60">
          <Loader2 size={20} className="animate-spin" />
        </div>
      ) : total === 0 ? (
        <div className="flex-1 flex items-center justify-center text-white/50 text-sm px-6 text-center">
          No channels to switch to.
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="flex-1 flex items-stretch gap-2 overflow-x-auto overflow-y-hidden
            px-4 pb-3 scroll-smooth [scrollbar-width:thin]"
        >
          {/* Labels and boxes are flat siblings of the scroll container so each
              box's `width: calc((100% - 2rem) / 5)` resolves against the strip. */}
          {shown.map((e) => (
            <Fragment key={e.c.key}>
              {e.groupStart && groupLabel(e.premium)}
              {box(e.c, e.premium)}
            </Fragment>
          ))}
        </div>
      )}
    </div>
  );
}
