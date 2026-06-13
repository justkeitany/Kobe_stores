import { useEffect, useState } from "react";
import { Tv, Activity, Server, Radio, Link2, Copy, Check, Zap } from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useServerStats } from "../hooks/useServerStats";
import api from "../lib/api";
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

  useEffect(() => {
    api.get("/streams/count").then((r) => setStreamCount(r.data.count)).catch(() => {});
    api.get("/categories").then((r) => setCategoryCount(r.data.length)).catch(() => {});
  }, []);

  return (
    <div className="p-6 space-y-5 max-w-[1400px]">

      {/* ── Page header ─────────────────────────────────────── */}
      <div className="flex items-center justify-between h-10">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Dashboard</h1>
        <span className={clsx(
          "inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1 rounded-full border",
          connected
            ? "bg-green-50 text-green-700 border-green-200"
            : "bg-gray-100 text-gray-500 border-gray-200"
        )}>
          <span className={clsx(
            "w-1.5 h-1.5 rounded-full",
            connected ? "bg-green-500 animate-pulse" : "bg-gray-400"
          )} />
          {connected ? "Live" : "Disconnected"}
        </span>
      </div>

      {/* ── Stat cards ──────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Total Streams"  value={streamCount}                   icon={<Tv size={17} />} />
        <StatCard label="Active Streams" value={stats?.active_streams ?? 0}    icon={<Activity size={17} />} />
        <StatCard label="CPU Usage"      value={`${stats?.cpu_percent ?? 0}%`} icon={<Server size={17} />} />
        <StatCard label="Categories"     value={categoryCount}                 icon={<Radio size={17} />} />
      </div>

      {/* ── Charts row ──────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <MetricChart
          label="CPU %"
          value={`${stats?.cpu_percent ?? 0}%`}
          dataKey="cpu"
          data={history}
          color="#111827"
        />
        <MetricChart
          label="RAM %"
          value={`${stats?.ram_percent ?? 0}%`}
          sub={stats ? `${stats.ram_used_mb} / ${stats.ram_total_mb} MB` : undefined}
          dataKey="ram"
          data={history}
          color="#111827"
        />
        <MetricChart
          label="Bandwidth Out"
          value={`${stats?.bw_out_kbps ?? 0} kbps`}
          dataKey="bw"
          data={history}
          color="#111827"
        />
      </div>

      {/* ── Quick Access Links ───────────────────────────────── */}
      <QuickAccessLinks />

      {/* ── Active FFmpeg processes ──────────────────────────── */}
      {stats && stats.streams.length > 0 && (
        <div className="card">
          <div className="flex items-center gap-2 mb-4">
            <Zap size={15} className="text-gray-400" />
            <h2 className="text-sm font-semibold text-gray-900">Active FFmpeg Processes</h2>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left">
                <th className="pb-2 text-xs font-medium text-gray-500">Stream</th>
                <th className="pb-2 text-xs font-medium text-gray-500">Status</th>
                <th className="pb-2 text-xs font-medium text-gray-500">Viewers</th>
              </tr>
            </thead>
            <tbody>
              {stats.streams.map((s) => (
                <tr key={s.id} className="border-b border-gray-100 table-row-hover">
                  <td className="py-2 text-gray-700 font-medium">#{s.id}</td>
                  <td className="py-2"><StatusBadge status={s.status} /></td>
                  <td className="py-2 text-gray-600">{s.viewers}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── Stat Card ───────────────────────────────────────────────── */
function StatCard({ label, value, icon }: {
  label: string; value: string | number; icon: React.ReactNode;
}) {
  return (
    <div className="card flex items-start justify-between">
      <div>
        <p className="text-xs font-medium text-gray-500 mb-1.5">{label}</p>
        <p className="text-2xl font-bold text-gray-900 tracking-tight">{value}</p>
      </div>
      <div className="w-9 h-9 rounded-lg border border-gray-200 bg-gray-50 flex items-center justify-center text-gray-500 shrink-0">
        {icon}
      </div>
    </div>
  );
}

/* ── Metric Chart ────────────────────────────────────────────── */
function MetricChart({ label, value, sub, dataKey, data, color }: {
  label: string; value: string; sub?: string;
  dataKey: string; data: StatPoint[]; color: string;
}) {
  return (
    <div className="card">
      <div className="flex items-start justify-between mb-4">
        <p className="text-xs font-medium text-gray-500">{label}</p>
        <div className="text-right">
          <p className="text-lg font-bold text-gray-900 tracking-tight">{value}</p>
          {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={72}>
        <AreaChart data={data} margin={{ top: 0, right: 0, left: -28, bottom: 0 }}>
          <defs>
            <linearGradient id={`g-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={color} stopOpacity={0.12} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" vertical={false} />
          <XAxis dataKey="t" hide />
          <YAxis tick={{ fontSize: 10, fill: "#9ca3af" }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{
              background: "#fff", border: "1px solid #e5e7eb",
              borderRadius: 8, fontSize: 12, color: "#111827",
            }}
            labelStyle={{ color: "#6b7280" }}
          />
          <Area
            type="monotone"
            dataKey={dataKey}
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#g-${dataKey})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ── Quick Access Links ──────────────────────────────────────── */
function QuickAccessLinks() {
  const base = "https://live.keitanyfrank.store";
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  function copy(key: string, value: string) {
    navigator.clipboard.writeText(value);
    setCopiedKey(key);
    toast.success("Copied");
    setTimeout(() => setCopiedKey(null), 2000);
  }

  const links = [
    { key: "panel",      label: "Admin Panel",       desc: "This dashboard",                          value: base },
    { key: "xtream",     label: "Xtream Server URL",  desc: "Enter in TiviMate / Smarters / GSE",     value: base },
    { key: "m3u",        label: "M3U Playlist",       desc: "Direct playlist URL for VLC, Kodi etc.", value: `${base}/get.php?username=admin&password=YOUR_PASS&type=m3u_plus` },
    { key: "xmltv",      label: "XMLTV / EPG",        desc: "Electronic programme guide URL",         value: `${base}/xmltv.php?username=admin&password=YOUR_PASS` },
    { key: "player_api", label: "Player API",         desc: "Xtream Codes authentication endpoint",   value: `${base}/player_api.php?username=admin&password=YOUR_PASS` },
    { key: "live",       label: "Live Stream",        desc: "Stream delivery URL pattern",            value: `${base}/live/admin/YOUR_PASS/{stream_id}.m3u8` },
  ];

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-4">
        <Link2 size={15} className="text-gray-400" />
        <h2 className="text-sm font-semibold text-gray-900">Quick Access Links</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {links.map((link) => (
          <div
            key={link.key}
            className="flex items-start justify-between gap-3 px-4 py-3 rounded-lg border border-gray-200 bg-gray-50 hover:bg-white hover:border-gray-300 transition-colors"
          >
            <div className="min-w-0">
              <p className="text-xs font-semibold text-gray-800">{link.label}</p>
              <p className="text-xs text-gray-400 mt-0.5 mb-1.5">{link.desc}</p>
              <code className="text-xs text-gray-600 break-all font-mono">{link.value}</code>
            </div>
            <button
              onClick={() => copy(link.key, link.value)}
              className="shrink-0 mt-0.5 p-1.5 rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-200 transition-colors"
              title="Copy"
            >
              {copiedKey === link.key
                ? <Check size={13} className="text-green-600" />
                : <Copy size={13} />}
            </button>
          </div>
        ))}
      </div>

      <p className="mt-4 text-xs text-gray-400">
        Replace <code className="font-mono text-gray-600 bg-gray-100 px-1 py-0.5 rounded">YOUR_PASS</code> with your admin password.{" "}
        <a href="/settings" className="text-gray-700 underline underline-offset-2 hover:text-gray-900">
          Update server URL in Settings →
        </a>
      </p>
    </div>
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
