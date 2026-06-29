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

  // The live programme should sit first on the left, so start scrolled to 0.
  useEffect(() => {
    if (!loading && scrollRef.current) scrollRef.current.scrollLeft = 0;
  }, [loading, data]);

  const now = data ? new Date(data.now).getTime() : Date.now();
  // Drop programmes that already finished — the one airing now leads the strip,
  // followed by what's coming up next.
  const progs = (data?.programmes ?? []).filter((p) => new Date(p.stop).getTime() > now);

  return (
    <div
      className="absolute bottom-0 left-0 right-0 z-30 h-1/4 min-h-[190px] flex flex-col"
      // Don't let taps/scrolls here toggle the video play state behind it —
      // the video keeps playing in the background while the guide is open.
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header — no background panel, so a drop-shadow keeps the channel name
          and logo legible over whatever is playing behind. */}
      <div className="flex items-center gap-2 px-4 pt-2.5 pb-1.5 shrink-0 drop-shadow-[0_1px_3px_rgba(0,0,0,0.9)]">
        <Tv size={15} className="text-red-500" />
        <span className="text-white text-sm font-semibold truncate">
          {data?.channel_name || "Guide"}
        </span>
        <span className="text-white/50 text-xs">· TV Guide</span>
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
          className="flex-1 flex items-stretch gap-2 overflow-x-auto overflow-y-hidden
            px-4 pb-3 scroll-smooth [scrollbar-width:thin]"
        >
          {progs.map((p, i) => {
            const start = new Date(p.start).getTime();
            const stop = new Date(p.stop).getTime();
            const isLive = now >= start && now < stop;
            const progress = isLive ? Math.min(1, Math.max(0, (now - start) / (stop - start))) : 0;
            return (
              <div
                key={i}
                // Exactly 5 boxes fit the row (1/5 of the width minus the four
                // 0.5rem gaps between them); scroll horizontally for the rest.
                style={{ flex: "0 0 auto", width: "calc((100% - 2rem) / 5)" }}
                // Solid-enough fill (~30-40%) over the video so text stays
                // readable; a light blur softens busy backgrounds behind it.
                className={`relative rounded-lg px-3 py-2 flex flex-col overflow-hidden border backdrop-blur-sm
                  ${isLive
                    ? "bg-red-600/30 border-red-500/70"
                    : "bg-black/40 border-white/15"}`}
              >
                <div className="flex items-center gap-1.5 text-[11px] mb-1 shrink-0">
                  {isLive && (
                    <span className="text-red-400 font-bold tracking-wide">● LIVE</span>
                  )}
                  <span className="text-white/55 tabular-nums">{hhmm(p.start)} – {hhmm(p.stop)}</span>
                </div>
                <div className="text-white text-[13px] font-semibold leading-tight line-clamp-2 shrink-0">
                  {p.title}
                </div>
                {/* Description fills whatever space is left in the box (clips if
                    it runs long) so boxes don't sit half-empty. */}
                {p.desc ? (
                  <p className="text-white/55 text-[11px] leading-snug mt-1 flex-1 min-h-0 overflow-hidden">
                    {p.desc}
                  </p>
                ) : p.category ? (
                  <p className="text-white/40 text-[11px] mt-1 flex-1 min-h-0 overflow-hidden">{p.category}</p>
                ) : null}
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
