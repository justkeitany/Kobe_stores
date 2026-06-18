import { useEffect, useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useServerStats } from "../hooks/useServerStats";
import { MIcon } from "../components/MIcon";
import api, { xtreamBaseUrl } from "../lib/api";
import { formatUptime } from "../lib/format";
import toast from "react-hot-toast";
import clsx from "clsx";

interface StatPoint { t: string; cpu: number; ram: number; bw: number; }

export default function Dashboard() {
  const { stats, connected } = useServerStats();
  const [history, setHistory] = useState<StatPoint[]>([]);
  const [streamCount, setStreamCount] = useState(0);
  const [categoryCount, setCategoryCount] = useState(0);

  useEffect(() => {
    if (!stats) return;
    setHistory((prev) => [
      ...prev.slice(-59),
      {
        t: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
        cpu: stats.cpu_percent,
        ram: stats.ram_percent,
        bw: Math.round(stats.bw_out_kbps),
      },
    ]);
  }, [stats]);

  function refreshCounts() {
    api.get("/streams/count").then((r) => setStreamCount(r.data.count)).catch(() => {});
    api.get("/categories").then((r) => setCategoryCount(r.data.length)).catch(() => {});
  }

  useEffect(() => {
    refreshCounts();
    const id = setInterval(refreshCounts, 7000);
    return () => clearInterval(id);
  }, []);

  const utilization = streamCount > 0
    ? Math.round(((stats?.active_streams ?? 0) / streamCount) * 100)
    : 0;

  return (
    <div className="p-lg space-y-lg max-w-[1400px]">

      {/* ── Page header ─────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-md">
        <div>
          <h2 className="font-headline-md text-headline-md font-bold mb-1">Dashboard</h2>
          <div className="flex items-center gap-md text-body-sm text-on-surface-variant">
            <span className="flex items-center">
              <span className={clsx("status-dot", connected ? "status-active" : "status-error")} />
              {connected ? "Server Online" : "Disconnected"}
            </span>
            {stats?.uptime_seconds != null && (
              <span className="border-l border-outline-variant pl-md">
                Uptime: {formatUptime(stats.uptime_seconds)}
              </span>
            )}
          </div>
        </div>
        <button onClick={refreshCounts} className="btn-secondary">
          <MIcon name="refresh" size={18} /> Refresh
        </button>
      </div>

      {/* ── Bento stat cards ────────────────────────────────── */}
      <div className="bento-grid">
        <StatCard className="col-span-6 lg:col-span-3"
          label="Total Streams" icon="live_tv" value={streamCount}
          sub="All resources operational" />
        <StatCard className="col-span-6 lg:col-span-3"
          label="Active Streams" icon="sensors" value={stats?.active_streams ?? 0}
          accent sub={`${utilization}% utilization`} />
        <StatCard className="col-span-6 lg:col-span-3"
          label="CPU Usage" icon="memory" value={`${stats?.cpu_percent ?? 0}%`}
          sub="Normal workload" subGreen />
        <StatCard className="col-span-6 lg:col-span-3"
          label="Categories" icon="folder_zip" value={categoryCount}
          sub="Unified structure" />
      </div>

      {/* ── Charts row ──────────────────────────────────────── */}
      <div className="bento-grid">
        <MetricChart className="col-span-12 lg:col-span-4"
          label="CPU LOAD %" value={`${stats?.cpu_percent ?? 0}% AVG`}
          dataKey="cpu" data={history} />
        <MetricChart className="col-span-12 lg:col-span-4"
          label="RAM USAGE %"
          value={stats ? `${stats.ram_used_mb} / ${stats.ram_total_mb} MB` : "—"}
          dataKey="ram" data={history} />
        <MetricChart className="col-span-12 lg:col-span-4"
          label="BANDWIDTH OUT" value={`${stats?.bw_out_kbps ?? 0} KBPS`}
          dataKey="bw" data={history} />
      </div>

      {/* ── Quick Access Links ──────────────────────────────── */}
      <QuickAccessLinks />

      {/* ── Active FFmpeg processes ─────────────────────────── */}
      {stats && stats.streams.length > 0 && (
        <section className="bg-surface-container border border-outline-variant p-lg">
          <div className="flex items-center gap-sm mb-md border-b border-outline-variant pb-md">
            <MIcon name="bolt" className="text-primary-fixed-dim" size={20} />
            <h3 className="font-headline-md text-headline-md font-bold">Active FFmpeg Processes</h3>
          </div>
          <table className="w-full text-body-sm">
            <thead>
              <tr className="text-left border-b border-outline-variant">
                <th className="pb-sm font-code-label uppercase text-[11px] tracking-wider text-on-surface-variant">Stream</th>
                <th className="pb-sm font-code-label uppercase text-[11px] tracking-wider text-on-surface-variant">Status</th>
                <th className="pb-sm font-code-label uppercase text-[11px] tracking-wider text-on-surface-variant text-right">Viewers</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/50">
              {stats.streams.map((s) => (
                <tr key={s.id} className="table-row-hover">
                  <td className="py-sm font-medium font-mono">#{s.id}</td>
                  <td className="py-sm"><StatusBadge status={s.status} /></td>
                  <td className="py-sm text-right font-code-label">{s.viewers}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

/* ── Stat Card ───────────────────────────────────────────────── */
function StatCard({ label, value, icon, sub, subGreen, accent, className }: {
  label: string; value: string | number; icon: string;
  sub?: string; subGreen?: boolean; accent?: boolean; className?: string;
}) {
  return (
    <div className={clsx("bg-surface-container-low border border-outline-variant p-md", className)}>
      <div className="flex justify-between items-start mb-2">
        <span className="font-code-label text-[10px] text-on-surface-variant uppercase">{label}</span>
        <MIcon name={icon} className="text-primary-fixed-dim" size={18} />
      </div>
      <div className={clsx("text-[28px] font-bold leading-none tracking-tight", accent && "text-primary")}>
        {value}
      </div>
      <div className={clsx("mt-1.5 text-[12px] text-on-surface-variant", subGreen && "text-green-400")}>
        {sub}
      </div>
    </div>
  );
}

/* ── Metric Chart ────────────────────────────────────────────── */
function MetricChart({ label, value, dataKey, data, className }: {
  label: string; value: string; dataKey: string; data: StatPoint[]; className?: string;
}) {
  return (
    <div className={clsx("bg-surface-container-low border border-outline-variant p-md", className)}>
      <div className="flex justify-between items-center mb-sm">
        <h3 className="font-code-label text-[10px] uppercase font-bold tracking-widest">{label}</h3>
        <span className="text-[12px] text-primary-fixed-dim font-bold">{value}</span>
      </div>
      <ResponsiveContainer width="100%" height={96}>
        <AreaChart data={data} margin={{ top: 4, right: 0, left: -28, bottom: 0 }}>
          <defs>
            <linearGradient id={`g-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#c6c6c6" stopOpacity={0.18} />
              <stop offset="100%" stopColor="#c6c6c6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-outline-variant)" vertical={false} />
          <XAxis dataKey="t" hide />
          <YAxis tick={{ fontSize: 10, fill: "var(--color-on-surface-variant)" }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface-container)", border: "1px solid var(--color-outline-variant)",
              borderRadius: 0, fontSize: 12, color: "var(--color-on-surface)",
            }}
            labelStyle={{ color: "var(--color-on-surface-variant)" }}
          />
          <Area
            type="monotone" dataKey={dataKey} stroke="#c6c6c6" strokeWidth={1.5}
            fill={`url(#g-${dataKey})`} dot={false} isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ── Quick Access Links ──────────────────────────────────────── */
function QuickAccessLinks() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState(xtreamBaseUrl(""));

  useEffect(() => {
    api.get("/settings")
      .then((r) => setServerUrl(xtreamBaseUrl(r.data?.server_url)))
      .catch(() => {});
  }, []);

  function copy(key: string, value: string) {
    navigator.clipboard.writeText(value);
    setCopiedKey(key);
    toast.success("Copied");
    setTimeout(() => setCopiedKey(null), 2000);
  }

  const playerBase = serverUrl;
  const links = [
    { key: "panel",      label: "Admin Panel",       desc: "Direct dashboard entry point",          value: window.location.origin },
    { key: "xtream",     label: "Xtream Server URL",  desc: "TiviMate / Smarters / GSE config",       value: playerBase },
    { key: "m3u",        label: "M3U Playlist",       desc: "Direct VLC / Kodi link",                 value: `${playerBase}/get.php?username=admin&password=YOUR_PASS&type=m3u_plus` },
    { key: "xmltv",      label: "XMLTV / EPG",        desc: "TV Program guide URL",                   value: `${playerBase}/xmltv.php?username=admin&password=YOUR_PASS` },
    { key: "player_api", label: "Player API",         desc: "Auth endpoint for apps",                 value: `${playerBase}/player_api.php?username=admin&password=YOUR_PASS` },
    { key: "live",       label: "Live Stream",        desc: "Stream delivery URL pattern",            value: `${playerBase}/live/admin/YOUR_PASS/{stream_id}.m3u8` },
  ];

  return (
    <section className="bg-surface-container border border-outline-variant p-lg">
      <div className="flex items-center gap-sm mb-lg border-b border-outline-variant pb-md">
        <MIcon name="link" className="text-primary-fixed-dim" size={20} />
        <h3 className="font-headline-md text-headline-md font-bold">Quick Access Links</h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-gutter">
        {links.map((link) => (
          <div
            key={link.key}
            className="group bg-surface-container-low border border-outline-variant p-md hover:border-primary transition-all"
          >
            <div className="flex justify-between items-start mb-sm">
              <div className="min-w-0">
                <h4 className="font-body-sm font-bold text-on-surface">{link.label}</h4>
                <p className="text-[12px] text-on-surface-variant">{link.desc}</p>
              </div>
              <button
                onClick={() => copy(link.key, link.value)}
                className="shrink-0 ml-2 text-on-surface-variant hover:text-primary transition-colors"
                title="Copy"
              >
                <MIcon name={copiedKey === link.key ? "check" : "content_copy"} size={18}
                  className={copiedKey === link.key ? "text-green-400" : undefined} />
              </button>
            </div>
            <code className="block font-code-label text-[11px] bg-surface-container-lowest p-2 text-primary-fixed-dim border border-outline-variant/30 overflow-x-auto whitespace-nowrap">
              {link.value}
            </code>
          </div>
        ))}
      </div>

      <div className="mt-lg pt-md border-t border-outline-variant/30 flex flex-col md:flex-row justify-between items-center text-on-surface-variant font-code-label text-[12px] gap-sm">
        <p>Replace <span className="bg-surface-variant px-1.5 py-0.5 text-primary-fixed-dim">YOUR_PASS</span> with your administrative password.</p>
        <a href="/settings" className="text-primary-fixed-dim hover:underline flex items-center gap-xs">
          Update server URL in Settings <MIcon name="arrow_forward" size={14} />
        </a>
      </div>
    </section>
  );
}

/* ── Status Badge ────────────────────────────────────────────── */
function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    running: "badge-green",
    starting: "badge-yellow",
    error: "badge-red",
    stopped: "badge-gray",
    idle: "badge-gray",
  };
  return <span className={map[status] ?? "badge-gray"}>{status}</span>;
}
