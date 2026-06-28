import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Heart, Tv } from "lucide-react";
import api from "../lib/api";

interface Programme {
  title: string;
  start: string;
  stop: string;
  desc?: string | null;
  category?: string | null;
}
interface Channel {
  id: number;
  name: string;
  logo: string;
  epg_channel_id: string;
  programmes: Programme[];
}
interface GuideResp {
  start: string;
  end: string;
  now: string;
  channels: Channel[];
}

const HOURS = 2; // window width → four 30-min columns, like tvguide.com
const SLOT_MIN = 30;
const CHAN_W = 150; // px, channel column

const TABS: { label: string; match: (c: string) => boolean }[] = [
  { label: "All", match: () => true },
  { label: "Sports", match: (c) => /sport|football|soccer|basketball|baseball|hockey|golf|racing|tennis|nascar|nba|nfl|mlb|boxing|ufc|wrestl/.test(c) },
  { label: "Movies", match: (c) => /movie|film/.test(c) },
  { label: "Family", match: (c) => /kid|child|family|animat|cartoon/.test(c) },
  { label: "News", match: (c) => /news/.test(c) },
];

function halfHourFloor(d: Date) {
  const x = new Date(d);
  x.setSeconds(0, 0);
  x.setMinutes(x.getMinutes() < 30 ? 0 : 30);
  return x;
}
const fmtTime = (d: Date) =>
  d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(/\s/, " ");

// Strip our internal prefixes for a clean network label under the logo.
const cleanName = (n: string) =>
  n.replace(/^(US|USA|UK|CA)[:\s]+/i, "").replace(/\s*\(\d+[ip]\).*$/i, "").trim() || n;

export default function EPGGuide() {
  const [anchor, setAnchor] = useState<number>(() => halfHourFloor(new Date()).getTime());
  const [tab, setTab] = useState(0);
  const [favs, setFavs] = useState<Set<number>>(new Set());

  const startUnix = Math.floor(anchor / 1000);
  const { data, isLoading } = useQuery<GuideResp>({
    queryKey: ["epg-guide", startUnix],
    queryFn: () => api.get(`/epg/guide?hours=${HOURS}&start=${startUnix}&limit=300`).then((r) => r.data),
    refetchInterval: 5 * 60 * 1000,
  });

  const winStart = anchor;
  const winEnd = anchor + HOURS * 3600 * 1000;
  const winDur = winEnd - winStart;
  const nowMs = data ? new Date(data.now).getTime() : Date.now();

  const slots = useMemo(() => {
    const out: Date[] = [];
    for (let t = winStart; t < winEnd; t += SLOT_MIN * 60 * 1000) out.push(new Date(t));
    return out;
  }, [winStart, winEnd]);

  const tabMatch = TABS[tab].match;
  const channels = (data?.channels ?? []).filter((ch) =>
    tab === 0 ? true : ch.programmes.some((p) => tabMatch((p.category || "").toLowerCase()))
  );
  const nowPct = nowMs >= winStart && nowMs <= winEnd ? ((nowMs - winStart) / winDur) * 100 : null;

  const toggleFav = (id: number) =>
    setFavs((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  return (
    <div className="px-6 py-5 max-w-[1500px] mx-auto">
      {/* Genre tabs */}
      <div className="flex items-center gap-6 mb-5">
        {TABS.map((t, i) => (
          <button
            key={t.label}
            onClick={() => setTab(i)}
            className={`text-[15px] font-bold transition ${
              i === tab
                ? "text-gray-900 px-4 py-1.5 rounded-full border border-gray-900"
                : "text-gray-500 hover:text-gray-800"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Time header */}
      <div className="flex items-center border-b border-gray-200">
        <div className="shrink-0 flex items-center" style={{ width: CHAN_W }}>
          <button
            className="p-2 text-gray-400 hover:text-gray-700"
            onClick={() => setAnchor(anchor - HOURS * 3600 * 1000)}
            aria-label="Earlier"
          >
            <ChevronLeft size={22} />
          </button>
        </div>
        <div className="flex-1 flex">
          {slots.map((s, i) => (
            <div key={s.getTime()} className="flex-1 py-3 relative">
              <span className={`text-[15px] ${i === 0 ? "font-bold text-gray-900" : "font-semibold text-gray-500"}`}>
                {i === 0 ? `NOW - ${fmtTime(s)}` : fmtTime(s)}
              </span>
              {i === 0 && <span className="absolute -bottom-px left-0 right-3 h-[3px] bg-gray-900 rounded-full" />}
            </div>
          ))}
        </div>
        <button
          className="shrink-0 p-2 text-gray-400 hover:text-gray-700"
          onClick={() => setAnchor(anchor + HOURS * 3600 * 1000)}
          aria-label="Later"
        >
          <ChevronRight size={22} />
        </button>
      </div>

      {/* Rows */}
      <div className="relative">
        {nowPct !== null && (
          <div
            className="absolute top-0 bottom-0 w-px bg-rose-500/60 z-20 pointer-events-none"
            style={{ left: `calc(${CHAN_W}px + (100% - ${CHAN_W}px - 40px) * ${nowPct / 100})` }}
          />
        )}

        {isLoading && <div className="py-16 text-center text-gray-400 text-sm">Loading guide…</div>}
        {!isLoading && channels.length === 0 && (
          <div className="py-16 text-center text-gray-400 text-sm">
            No programmes in this window. Map channels to EPG sources on the EPG page first.
          </div>
        )}

        {channels.map((ch) => (
          <div key={ch.id} className="flex items-stretch border-b border-gray-200 min-h-[92px]">
            {/* Channel cell: logo + abbrev + heart */}
            <div className="shrink-0 flex items-center gap-1 pr-2" style={{ width: CHAN_W }}>
              <div className="flex flex-col items-center justify-center w-[68px] py-2">
                {ch.logo ? (
                  <img src={ch.logo} alt="" className="w-10 h-10 rounded-full object-contain bg-white ring-1 ring-gray-100" />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-gray-100 grid place-items-center">
                    <Tv size={18} className="text-gray-400" />
                  </div>
                )}
                <span className="mt-1 text-[11px] font-semibold text-gray-500 text-center leading-tight line-clamp-1 w-full">
                  {cleanName(ch.name)}
                </span>
              </div>
              <button onClick={() => toggleFav(ch.id)} className="p-1 text-gray-300 hover:text-rose-400" aria-label="Favorite">
                <Heart size={18} className={favs.has(ch.id) ? "fill-rose-500 text-rose-500" : ""} />
              </button>
            </div>

            {/* Timeline */}
            <div className="relative flex-1 border-l border-gray-200">
              {ch.programmes.map((p, idx) => {
                const ps = new Date(p.start).getTime();
                const pe = new Date(p.stop).getTime();
                const left = Math.max(0, ((ps - winStart) / winDur) * 100);
                const right = Math.min(100, ((pe - winStart) / winDur) * 100);
                const width = right - left;
                if (width <= 0.3) return null;
                const live = nowMs >= ps && nowMs < pe;
                const dim = tab !== 0 && !tabMatch((p.category || "").toLowerCase());
                return (
                  <div
                    key={idx}
                    className={`absolute top-0 bottom-0 flex flex-col justify-center px-4 border-l border-gray-200 first:border-l-0 overflow-hidden ${dim ? "opacity-25" : ""}`}
                    style={{ left: `${left}%`, width: `${width}%` }}
                    title={`${p.title}\n${fmtTime(new Date(ps))} - ${fmtTime(new Date(pe))}${p.desc ? "\n\n" + p.desc : ""}`}
                  >
                    <div className="text-[15px] font-bold text-gray-900 leading-snug line-clamp-2">{p.title}</div>
                    <div className="text-[13px] mt-1 leading-none">
                      {live && <span className="text-fuchsia-600 font-bold mr-1.5">LIVE</span>}
                      <span className="text-gray-500">
                        {fmtTime(new Date(ps))} - {fmtTime(new Date(pe))}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
