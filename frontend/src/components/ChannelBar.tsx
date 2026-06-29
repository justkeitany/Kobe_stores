import { useEffect, useRef, useState } from "react";
import { X, Loader2, LayoutGrid, Tv } from "lucide-react";
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
// channels a viewer can actually open lead the strip.
const HEALTH_ORDER: Record<string, number> = {
  online: 0, checking: 1, geo: 2, offline: 3, dead: 4,
};

/**
 * In-player channel switcher. Mirrors the EPG strip: pops up over the bottom
 * quarter while the video keeps playing behind it, and the user swipes/scrolls
 * left-right through every channel. Tapping a box switches the player to that
 * channel in place — no going back to the channels page. Self-contained: fetches
 * the channel directory itself.
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
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [switching, setSwitching] = useState<string | null>(null); // key being opened
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<Channel[]>("/channels")
      .then((r) => { if (!cancelled) setChannels(r.data); })
      .catch(() => { if (!cancelled) setChannels(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // Only channels we can actually open (an imported stream id, or a raw url),
  // watchable ones first; keep alphabetical order within a health tier.
  const list = (channels ?? [])
    .filter((c) => (c.imported && c.stream_id != null) || !!c.url)
    .map((c, i) => ({ c, i }))
    .sort((a, b) => (HEALTH_ORDER[a.c.health] ?? 9) - (HEALTH_ORDER[b.c.health] ?? 9) || a.i - b.i)
    .map((x) => x.c);

  // Bring the channel that's playing now into view so the user starts from where
  // they are rather than the top of the alphabet.
  useEffect(() => {
    if (loading || !scrollRef.current || Number.isNaN(currentStreamId)) return;
    const el = scrollRef.current.querySelector<HTMLElement>('[data-current="1"]');
    if (el) el.scrollIntoView({ inline: "center", block: "nearest" });
  }, [loading, channels, currentStreamId]);

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
      if (token) onPick(token, c.name, c.imported ? c.stream_id : null);
    } catch {
      setSwitching(null);
    }
  }

  return (
    <div
      className="absolute bottom-0 left-0 right-0 z-30 h-1/4 min-h-[190px] flex flex-col"
      // Don't let taps/scrolls here toggle the video play state behind it.
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
      ) : list.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-white/50 text-sm px-6 text-center">
          No channels to switch to.
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="flex-1 flex items-stretch gap-2 overflow-x-auto overflow-y-hidden
            px-4 pb-3 scroll-smooth [scrollbar-width:thin]"
        >
          {list.map((c) => {
            const isCurrent =
              !Number.isNaN(currentStreamId) && c.stream_id === currentStreamId;
            const isSwitching = switching === c.key;
            return (
              <button
                key={c.key}
                data-current={isCurrent ? "1" : undefined}
                onClick={() => pick(c)}
                // Exactly 5 boxes fit the row (matches the EPG strip); scroll for
                // the rest. Everything inside is centered.
                style={{ flex: "0 0 auto", width: "calc((100% - 2rem) / 5)" }}
                className={`relative rounded-lg px-3 py-2 flex flex-col items-center justify-center text-center
                  gap-1.5 overflow-hidden border backdrop-blur-sm transition-colors
                  ${isCurrent
                    ? "bg-red-600/30 border-red-500/70"
                    : "bg-black/40 border-white/15 hover:bg-black/55 hover:border-white/30"}`}
              >
                {/* Logo (or a fallback TV glyph), centered. */}
                <div className="h-10 flex items-center justify-center shrink-0">
                  {c.logo ? (
                    <img
                      src={c.logo}
                      alt=""
                      loading="lazy"
                      className="max-h-10 max-w-[72px] object-contain"
                      onError={(e) => { (e.currentTarget.style.display = "none"); }}
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
          })}
        </div>
      )}
    </div>
  );
}
