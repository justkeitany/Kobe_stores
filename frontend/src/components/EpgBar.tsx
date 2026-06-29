import { useEffect, useRef, useState } from "react";
import { X, Loader2, Tv } from "lucide-react";
import api from "../lib/api";

interface Programme {
  title: string;
  start: string; // ISO
  stop: string;  // ISO
  desc?: string | null;
  category?: string | null;
}

interface Timeline {
  now: string;
  channel_name: string;
  epg_channel_id: string;
  programmes: Programme[];
}

// Horizontal scale: pixels per minute. Wide enough that a 30-min show is a
// comfortable tap target and a movie reads as visibly longer.
const PX_PER_MIN = 5;
const MIN_CARD_PX = 96;

function hhmm(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * In-player EPG strip. Pops up over the bottom quarter of the screen while the
 * video keeps playing behind it; the user scrolls left/right through past, live
 * and upcoming programmes. Self-contained: fetches its own data for `streamId`.
 */
export default function EpgBar({ streamId, onClose }: { streamId: number; onClose: () => void }) {
  const [data, setData] = useState<Timeline | null>(null);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const liveRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<Timeline>(`/epg/timeline/${streamId}`)
      .then((r) => { if (!cancelled) setData(r.data); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [streamId]);

  // Center the live programme once the strip has rendered.
  useEffect(() => {
    if (!loading && liveRef.current) {
      liveRef.current.scrollIntoView({ inline: "center", block: "nearest", behavior: "auto" });
    }
  }, [loading, data]);

  const now = data ? new Date(data.now).getTime() : Date.now();
  const progs = data?.programmes ?? [];

  return (
    <div
      className="absolute bottom-0 left-0 right-0 z-30 h-1/4 min-h-[150px]
        bg-gradient-to-t from-black via-black/95 to-black/70 backdrop-blur-sm
        border-t border-white/10 flex flex-col"
      // Don't let taps/scrolls here toggle the video play state behind it.
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 pt-2.5 pb-1.5 shrink-0">
        <Tv size={15} className="text-red-500" />
        <span className="text-white text-sm font-semibold truncate">
          {data?.channel_name || "Guide"}
        </span>
        <span className="text-white/40 text-xs">· TV Guide</span>
        <div className="flex-1" />
        <button
          onClick={onClose}
          className="text-white/70 hover:text-white p-1 -mr-1 transition-colors"
          title="Close guide"
        >
          <X size={18} />
        </button>
      </div>

      {/* Strip */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center text-white/60">
          <Loader2 size={20} className="animate-spin" />
        </div>
      ) : progs.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-white/50 text-sm px-6 text-center">
          No guide data for this channel yet.
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="flex-1 flex items-stretch gap-1.5 overflow-x-auto overflow-y-hidden
            px-4 pb-3 scroll-smooth [scrollbar-width:thin]"
        >
          {progs.map((p, i) => {
            const start = new Date(p.start).getTime();
            const stop = new Date(p.stop).getTime();
            const durMin = Math.max(1, (stop - start) / 60000);
            const isLive = now >= start && now < stop;
            const isPast = stop <= now;
            const width = Math.max(MIN_CARD_PX, Math.round(durMin * PX_PER_MIN));
            const progress = isLive ? Math.min(1, Math.max(0, (now - start) / (stop - start))) : 0;
            return (
              <div
                key={i}
                ref={isLive ? liveRef : undefined}
                style={{ width }}
                className={`relative shrink-0 rounded-lg px-3 py-2 flex flex-col overflow-hidden border
                  ${isLive
                    ? "bg-red-500/15 border-red-500/60"
                    : isPast
                    ? "bg-white/5 border-white/10 opacity-50"
                    : "bg-white/[0.07] border-white/10"}`}
              >
                <div className="flex items-center gap-1.5 text-[11px] mb-0.5">
                  {isLive && (
                    <span className="text-red-400 font-bold tracking-wide">● LIVE</span>
                  )}
                  <span className="text-white/55 tabular-nums">{hhmm(p.start)}</span>
                </div>
                <div className="text-white text-[13px] font-medium leading-tight line-clamp-2">
                  {p.title}
                </div>
                {p.category && (
                  <div className="text-white/40 text-[11px] mt-auto pt-1 truncate">{p.category}</div>
                )}
                {isLive && (
                  <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-white/10">
                    <div className="h-full bg-red-500" style={{ width: `${progress * 100}%` }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
