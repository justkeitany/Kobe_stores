import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, AlertCircle, ExternalLink, Tv } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { MIcon } from "../components/MIcon";
import clsx from "clsx";

interface Channel {
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

const PAGE = 24;
type Health = "online" | "offline" | "geo" | "checking";

const BADGE: Record<Health, { label: string; cls: string; dot: string }> = {
  online:   { label: "Online",         cls: "bg-[#1f3a2a] text-[#5edc8a]", dot: "bg-[#5edc8a]" },
  offline:  { label: "Offline",        cls: "bg-[#3a1f1f] text-[#ffb4ab]", dot: "bg-[#ffb4ab]" },
  geo:      { label: "Geo-restricted", cls: "bg-surface-container text-on-surface-variant", dot: "bg-[#9aa0a6]" },
  checking: { label: "Checking…",      cls: "bg-surface-container text-on-surface-variant/70", dot: "bg-on-surface-variant/40" },
};

export default function Channels() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [working, setWorking] = useState<Set<string>>(new Set());
  const [probed, setProbed] = useState<Record<string, Health>>({});

  const { data: channels = [], isLoading } = useQuery<Channel[]>({
    queryKey: ["all-channels"],
    queryFn: () => api.get("/channels").then((r) => r.data),
    refetchInterval: 60_000,
  });

  function healthOf(c: Channel): Health {
    return probed[c.key] || c.health;
  }

  const counts = useMemo(() => {
    const c = { online: 0, offline: 0, geo: 0, checking: 0 } as Record<Health, number>;
    for (const ch of channels) c[probed[ch.key] || ch.health]++;
    return c;
  }, [channels, probed]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? channels.filter((c) => c.name.toLowerCase().includes(q)) : channels;
  }, [channels, search]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const shown = filtered.slice(cur * PAGE, cur * PAGE + PAGE);

  async function reportIssue(c: Channel) {
    setWorking((w) => new Set(w).add(c.key));
    try {
      if (c.imported && c.stream_id != null) {
        const r = await api.post(`/ai/diagnose/${c.stream_id}`);
        const d = r.data;
        const fixed = d?.recommended_action && d.recommended_action !== "none";
        toast.success(`${c.name}: ${String(d.cause || "checked").replace(/_/g, " ")}${fixed ? ` → ${d.recommended_action.replace(/_/g, " ")}` : ""}`, { duration: 6000 });
        qc.invalidateQueries({ queryKey: ["ai-notifications"] });
        qc.invalidateQueries({ queryKey: ["all-channels"] });
      } else {
        const r = await api.post("/channels/probe", { url: c.url, name: c.name });
        const st = r.data.status as Health | "skipped";
        if (st === "skipped") {
          toast(r.data.note);
        } else {
          setProbed((p) => ({ ...p, [c.key]: st }));
          toast.success(`${c.name}: ${st} — ${r.data.note}`, { duration: 6000 });
          qc.invalidateQueries({ queryKey: ["ai-notifications"] });
        }
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Couldn't check this channel");
    } finally {
      setWorking((w) => { const n = new Set(w); n.delete(c.key); return n; });
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
            onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
        </div>
      </div>

      {isLoading ? (
        <div className="py-16 flex justify-center text-on-surface-variant"><Loader2 size={24} className="animate-spin" /></div>
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-on-surface-variant">No channels. Add a playlist or import streams.</div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-gutter">
            {shown.map((c) => {
              const b = BADGE[healthOf(c)];
              const busy = working.has(c.key);
              return (
                <div key={c.key} className="bg-surface-container-low border border-outline-variant rounded-md flex flex-col overflow-hidden">
                  <div className="p-md flex flex-col items-center text-center gap-2 flex-1">
                    <div className="self-stretch flex items-center justify-between">
                      <span className={clsx("inline-flex items-center gap-1.5 text-[11px] font-medium rounded-full px-2 py-0.5", b.cls)}>
                        <span className={clsx("w-1.5 h-1.5 rounded-full", b.dot)} /> {b.label}
                      </span>
                      <button onClick={() => reportIssue(c)} disabled={busy} title="Re-check"
                        className="text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-50">
                        <RefreshCw size={15} className={clsx(busy && "animate-spin")} />
                      </button>
                    </div>
                    <Logo logo={c.logo} />
                    <p className="font-bold leading-tight line-clamp-2">{c.name}</p>
                    <span className="text-[10px] font-code-label uppercase tracking-wider text-on-surface-variant border border-outline-variant rounded-full px-2 py-0.5 truncate max-w-full">
                      {c.source}
                    </span>
                  </div>
                  <div className="border-t border-outline-variant p-3 space-y-2">
                    <button onClick={() => reportIssue(c)} disabled={busy}
                      className="w-full inline-flex items-center justify-center gap-1.5 text-[13px] font-medium rounded-md border border-error/40 text-error py-2 hover:bg-error/10 transition-colors disabled:opacity-50">
                      {busy ? <Loader2 size={14} className="animate-spin" /> : <AlertCircle size={14} />}
                      {busy ? "AI checking…" : "Issues?"}
                    </button>
                    {c.imported && c.stream_id != null ? (
                      <button onClick={() => window.open(`${window.location.origin}/hls/${c.stream_id}/master.m3u8`, "_blank")}
                        className="w-full inline-flex items-center justify-center gap-1.5 text-[13px] text-on-surface-variant hover:text-on-surface transition-colors">
                        Watch in browser <ExternalLink size={13} />
                      </button>
                    ) : (
                      <p className="text-center text-[11px] text-on-surface-variant/60">Not imported to streams</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {pages > 1 && (
            <div className="flex items-center justify-center gap-3 pt-2 text-body-sm">
              <button className="btn-secondary py-1" onClick={() => setPage(Math.max(0, cur - 1))} disabled={cur === 0}>Prev</button>
              <span className="text-on-surface-variant">Page {cur + 1} of {pages}</span>
              <button className="btn-secondary py-1" onClick={() => setPage(Math.min(pages - 1, cur + 1))} disabled={cur >= pages - 1}>Next</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Logo({ logo }: { logo?: string | null }) {
  const [failed, setFailed] = useState(false);
  if (!logo || failed) {
    return (
      <div className="w-16 h-16 rounded-lg border border-outline-variant flex items-center justify-center bg-surface-container">
        <Tv size={24} className="text-on-surface-variant" />
      </div>
    );
  }
  return (
    <img src={logo} alt="" onError={() => setFailed(true)}
      className="w-16 h-16 rounded-lg object-contain border border-outline-variant bg-white p-1" />
  );
}
