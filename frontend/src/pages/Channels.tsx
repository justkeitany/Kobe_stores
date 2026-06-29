import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Tv } from "lucide-react";
import toast from "react-hot-toast";
import api, { mintStreamToken } from "../lib/api";
import { MIcon } from "../components/MIcon";
import { Pagination } from "../components/Pagination";
import { LogoCard } from "../components/LogoCard";

const PAGE_SIZE = 36;

export interface Channel {
  key: string;
  stream_id: number | null;
  name: string;
  logo: string;
  source: string;
  imported: boolean;
  is_enabled: boolean;
  health: Health;
  url?: string;
}

export type Health = "online" | "offline" | "geo" | "dead" | "checking";

// Surface what viewers can actually watch first — online, then still-checking,
// then geo-blocked, then offline, then dead (unreachable upstream).
const HEALTH_ORDER: Record<Health, number> = { online: 0, checking: 1, geo: 2, offline: 3, dead: 4 };

export const BADGE: Record<Health, { label: string; cls: string; dot: string }> = {
  online:   { label: "Online",         cls: "bg-[#1f3a2a] text-[#5edc8a]", dot: "bg-[#5edc8a]" },
  offline:  { label: "Offline",        cls: "bg-[#3d2e0a] text-[#f5a623]", dot: "bg-[#f5a623]" },
  geo:      { label: "Geo-restricted", cls: "bg-surface-container text-on-surface-variant", dot: "bg-[#9aa0a6]" },
  dead:     { label: "Dead",           cls: "bg-[#1a1a1a] text-[#ff6b6b]", dot: "bg-[#ff6b6b]" },
  checking: { label: "Pending",        cls: "bg-surface-container text-on-surface-variant/70", dot: "bg-on-surface-variant/40" },
};

export default function Channels() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const nav = useNavigate();

  const { data: channels = [], isLoading } = useQuery<Channel[]>({
    queryKey: ["all-channels"],
    queryFn: () => api.get("/channels").then((r) => r.data),
    refetchInterval: 60_000,
  });

  const counts = useMemo(() => {
    const c = { online: 0, offline: 0, geo: 0, dead: 0, checking: 0 } as Record<Health, number>;
    for (const ch of channels) c[ch.health]++;
    return c;
  }, [channels]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = q ? channels.filter((c) => c.name.toLowerCase().includes(q)) : channels.slice();
    // Surface watchable channels first; keep original order within a health tier.
    return list
      .map((c, i) => ({ c, i }))
      .sort((a, b) => HEALTH_ORDER[a.c.health] - HEALTH_ORDER[b.c.health] || a.i - b.i)
      .map((x) => x.c);
  }, [channels, search]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const cur = Math.min(page, pages - 1);
  const shown = filtered.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);

  // Reset to page 0 on new search
  useMemo(() => { setPage(0); }, [search]);

  async function playChannel(c: Channel) {
    try {
      const token =
        c.imported && c.stream_id != null
          ? await mintStreamToken({ stream_id: c.stream_id })
          : c.url
          ? await mintStreamToken({ url: c.url })
          : null;
      // Pass the stream id so the player can pull this channel's EPG strip.
      const sid = c.imported && c.stream_id != null ? `&sid=${c.stream_id}` : "";
      if (token) nav(`/watch?t=${token}&name=${encodeURIComponent(c.name)}${sid}`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Couldn't open this channel");
    }
  }

  return (
    <div className="p-lg space-y-md max-w-[1500px]">
      <div className="flex justify-between items-end flex-wrap gap-md">
        <div>
          <h2 className="text-lg font-bold tracking-tight mb-0.5">Channels</h2>
          <p className="text-on-surface-variant text-[12px] flex items-center gap-2 flex-wrap">
            <span>{isLoading ? "Loading…" : `${filtered.length} channels`}</span>
            {!isLoading && (
              <span className="flex items-center gap-2">
                <span className="text-[#5edc8a]">● {counts.online} online</span>
                <span className="text-[#ffb4ab]">● {counts.offline} offline</span>
                <span className="text-[#9aa0a6]">● {counts.geo} geo</span>
                {counts.checking > 0 && <span className="opacity-60">● {counts.checking} checking…</span>}
              </span>
            )}
          </p>
          <p className="text-on-surface-variant/70 text-[11px] mt-0.5">The AI probes every channel in the background and auto-fixes broken ones.</p>
        </div>
        <div className="relative w-full sm:w-72">
          <MIcon name="search" size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
          <input className="input pl-10" placeholder="Search channels…" value={search}
            onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>

      {isLoading ? (
        <div className="py-16 flex justify-center text-on-surface-variant"><Loader2 size={24} className="animate-spin" /></div>
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-on-surface-variant">No channels. Add a playlist or import streams.</div>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-5">
            {shown.map((c) => (
              <LogoCard key={c.key} name={c.name} logo={c.logo} onClick={() => playChannel(c)} />
            ))}
          </div>

          <div className="pt-3">
            <Pagination
              page={cur + 1}
              totalPages={pages}
              onChange={(p) => setPage(p - 1)}
            />
          </div>
        </>
      )}
    </div>
  );
}

export function Logo({ logo }: { logo?: string | null }) {
  const [failed, setFailed] = useState(false);
  if (!logo || failed) {
    return (
      <div className="w-10 h-10 rounded-md border border-outline-variant flex items-center justify-center bg-surface-container">
        <Tv size={16} className="text-on-surface-variant" />
      </div>
    );
  }
  return (
    <img src={logo} alt="" onError={() => setFailed(true)} loading="lazy" decoding="async"
      className="w-10 h-10 rounded-md object-contain border border-outline-variant bg-white p-0.5" />
  );
}
