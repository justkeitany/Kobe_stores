import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, AlertCircle, ExternalLink, Tv } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { MIcon } from "../components/MIcon";
import clsx from "clsx";

interface Stream {
  id: number;
  name: string;
  logo_url?: string | null;
  status: string;
  is_enabled: boolean;
  category_id?: number | null;
}
interface Category { id: number; name: string; }
interface Alert { id: number; stream_id: number | null; title: string; detail: string | null; auto_applied: boolean; created_at: string; data?: any; }

const PAGE = 24;

type Health = "live" | "offline" | "geo" | "idle";

function healthOf(s: Stream, cause?: string): Health {
  if (!s.is_enabled) return "offline";
  if (cause === "geo_blocked") return "geo";
  if (s.status === "running") return "live";
  if (s.status === "error") return "offline";
  return "idle";
}

const BADGE: Record<Health, { label: string; cls: string; dot: string }> = {
  live:    { label: "Live Now",    cls: "bg-[#1f3a2a] text-[#5edc8a]", dot: "bg-[#5edc8a]" },
  offline: { label: "Offline",     cls: "bg-[#3a1f1f] text-[#ffb4ab]", dot: "bg-[#ffb4ab]" },
  geo:     { label: "Geo-blocked", cls: "bg-surface-container text-on-surface-variant", dot: "bg-[#f5c86e]" },
  idle:    { label: "Idle",        cls: "bg-surface-container text-on-surface-variant", dot: "bg-on-surface-variant/50" },
};

export default function Channels() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [working, setWorking] = useState<Set<number>>(new Set());

  const { data: streams = [], isLoading } = useQuery<Stream[]>({
    queryKey: ["streams"],
    queryFn: () => api.get("/streams").then((r) => r.data),
  });
  const { data: cats = [] } = useQuery<Category[]>({
    queryKey: ["categories"],
    queryFn: () => api.get("/categories").then((r) => r.data),
  });
  // Recent AI alerts → map stream → latest cause/feedback for badges + inline note.
  const { data: alerts = [] } = useQuery<Alert[]>({
    queryKey: ["ai-notifications"],
    queryFn: () => api.get("/ai/notifications?limit=100").then((r) => r.data),
    refetchInterval: 20_000,
  });

  const catName = useMemo(() => {
    const m = new Map<number, string>();
    cats.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [cats]);

  const latestAlert = useMemo(() => {
    const m = new Map<number, Alert>();
    for (const a of alerts) if (a.stream_id != null && !m.has(a.stream_id)) m.set(a.stream_id, a);
    return m;
  }, [alerts]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? streams.filter((s) => s.name.toLowerCase().includes(q)) : streams;
  }, [streams, search]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const shown = filtered.slice(cur * PAGE, cur * PAGE + PAGE);

  async function reportIssue(s: Stream) {
    setWorking((w) => new Set(w).add(s.id));
    try {
      const r = await api.post(`/ai/diagnose/${s.id}`);
      const d = r.data;
      const fixed = d?.recommended_action && d.recommended_action !== "none";
      toast.success(
        `${s.name}: ${String(d.cause || "checked").replace(/_/g, " ")}${fixed ? ` → ${d.recommended_action.replace(/_/g, " ")}` : ""}`,
        { duration: 6000 }
      );
      qc.invalidateQueries({ queryKey: ["ai-notifications"] });
      qc.invalidateQueries({ queryKey: ["streams"] });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "AI couldn't check this channel");
    } finally {
      setWorking((w) => { const n = new Set(w); n.delete(s.id); return n; });
    }
  }

  function watch(s: Stream) {
    window.open(`${window.location.origin}/hls/${s.id}/master.m3u8`, "_blank");
  }

  return (
    <div className="p-lg space-y-md max-w-[1500px]">
      <div className="flex justify-between items-end flex-wrap gap-md">
        <div>
          <h2 className="text-lg font-bold tracking-tight mb-0.5">Channels</h2>
          <p className="text-on-surface-variant text-[12px]">
            {isLoading ? "Loading…" : `${filtered.length} channel${filtered.length === 1 ? "" : "s"}`}
            <span className="ml-2 opacity-70">· AI watches these in the background and fixes issues automatically</span>
          </p>
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
        <div className="py-16 text-center text-on-surface-variant">No channels. Import some from Streams or Playlists.</div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-gutter">
            {shown.map((s) => {
              const a = latestAlert.get(s.id);
              const h = healthOf(s, a?.data?.cause);
              const b = BADGE[h];
              const busy = working.has(s.id);
              return (
                <div key={s.id} className="bg-surface-container-low border border-outline-variant rounded-md flex flex-col overflow-hidden">
                  <div className="p-md flex flex-col items-center text-center gap-2 flex-1">
                    <div className="self-stretch flex items-center justify-between">
                      <span className={clsx("inline-flex items-center gap-1.5 text-[11px] font-medium rounded-full px-2 py-0.5", b.cls)}>
                        <span className={clsx("w-1.5 h-1.5 rounded-full", b.dot)} /> {b.label}
                      </span>
                      <button onClick={() => reportIssue(s)} disabled={busy} title="Re-check with AI"
                        className="text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-50">
                        <RefreshCw size={15} className={clsx(busy && "animate-spin")} />
                      </button>
                    </div>
                    <Logo logo={s.logo_url} />
                    <p className="font-bold leading-tight">{s.name}</p>
                    {s.category_id != null && catName.get(s.category_id) && (
                      <span className="text-[10px] font-code-label uppercase tracking-wider text-on-surface-variant border border-outline-variant rounded-full px-2 py-0.5">
                        {catName.get(s.category_id)}
                      </span>
                    )}
                    {a && (
                      <p className={clsx("text-[11px] mt-0.5 line-clamp-2", a.auto_applied ? "text-[#5edc8a]" : "text-on-surface-variant")}>
                        {a.auto_applied ? "✓ " : ""}{a.title.replace(`${s.name}: `, "")}
                      </p>
                    )}
                  </div>
                  <div className="border-t border-outline-variant p-3 space-y-2">
                    <button onClick={() => reportIssue(s)} disabled={busy}
                      className="w-full inline-flex items-center justify-center gap-1.5 text-[13px] font-medium rounded-md border border-error/40 text-error py-2 hover:bg-error/10 transition-colors disabled:opacity-50">
                      {busy ? <Loader2 size={14} className="animate-spin" /> : <AlertCircle size={14} />}
                      {busy ? "AI checking…" : "Issues?"}
                    </button>
                    <button onClick={() => watch(s)}
                      className="w-full inline-flex items-center justify-center gap-1.5 text-[13px] text-on-surface-variant hover:text-on-surface transition-colors">
                      Watch in browser <ExternalLink size={13} />
                    </button>
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
