import { useEffect, useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { useServerStats } from "../hooks/useServerStats";
import { MIcon } from "../components/MIcon";
import { formatUptime } from "../lib/format";
import api from "../lib/api";
import toast from "react-hot-toast";
import clsx from "clsx";

export default function ServerPage() {
  const { stats } = useServerStats();
  const [history, setHistory] = useState<any[]>([]);
  const [logs, setLogs] = useState("");
  const [loadingLogs, setLoadingLogs] = useState(false);

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

  async function fetchLogs() {
    setLoadingLogs(true);
    try {
      const r = await api.get("/server/logs", { params: { lines: 100 } });
      setLogs(r.data.logs);
    } catch {
      toast.error("Could not fetch logs");
    } finally {
      setLoadingLogs(false);
    }
  }

  async function restartAll() {
    if (!confirm("Stop all active FFmpeg streams? They'll restart on next viewer connection.")) return;
    await api.post("/server/restart-all-streams");
    toast.success("All streams stopped");
  }

  const viewers = stats?.streams.reduce((sum, s) => sum + s.viewers, 0) ?? 0;

  return (
    <div className="p-lg space-y-lg">
      <div className="flex items-end justify-between flex-wrap gap-md">
        <div>
          <h2 className="font-headline-md text-headline-md font-bold tracking-tight mb-1">Server Monitor</h2>
          <p className="text-on-surface-variant text-body-sm">Live system telemetry and process control.</p>
        </div>
        <div className="flex gap-md">
          <button className="btn-secondary" onClick={fetchLogs} disabled={loadingLogs}>
            <MIcon name="description" size={18} /> View Logs
          </button>
          <button className="btn-danger" onClick={restartAll}>
            <MIcon name="stop_circle" size={18} /> Restart All Streams
          </button>
        </div>
      </div>

      {/* Bento stat cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-gutter">
        <StatCard label="CPU Load" icon="developer_board"
          value={stats?.cpu_percent ?? 0} unit="%" />
        <StatCard label="RAM Usage" icon="memory"
          value={stats?.ram_percent ?? 0} unit="%"
          sub={stats ? `${stats.ram_used_mb} / ${stats.ram_total_mb} MB` : undefined} />
        <StatCard label="Bandwidth Out" icon="cell_tower"
          value={stats?.bw_out_kbps ?? 0} unit="kbps" />
        <StatCard label="Active Streams" icon="sensors"
          value={stats?.active_streams ?? 0} />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-gutter">
        <ChartCard label="CPU % (LAST 60M)" dataKey="cpu" data={history} />
        <ChartCard label="RAM % (LAST 60M)" dataKey="ram" data={history} />
        <ChartCard label="BANDWIDTH OUT (kbps)" dataKey="bw" data={history} />
      </div>

      {/* Footer cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-gutter">
        <FooterCard label="Active Viewers" icon="visibility" value={viewers.toLocaleString()} />
        <FooterCard label="System Load" icon="speed" value={`${stats?.cpu_percent ?? 0}%`} />
        <FooterCard label="Uptime" icon="schedule"
          value={stats?.uptime_seconds != null ? formatUptime(stats.uptime_seconds) : "—"} />
      </div>

      {/* Active FFmpeg processes */}
      {stats && stats.streams.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant p-lg">
          <h3 className="font-code-label text-code-label uppercase tracking-widest text-on-surface-variant mb-md">
            FFmpeg Processes
          </h3>
          <div className="space-y-2">
            {stats.streams.map((s: any) => (
              <div key={s.id}
                className="flex items-center justify-between px-3 py-2 bg-surface-container border border-outline-variant text-body-sm">
                <span className="font-code-label">Stream #{s.id}</span>
                <span className={clsx("text-[12px] uppercase font-bold tracking-widest",
                  s.status === "running" ? "text-green-500" :
                  s.status === "error" ? "text-red-400" : "text-on-surface-variant")}>
                  {s.status}
                </span>
                <span className="text-on-surface-variant text-[12px] font-code-label">
                  {s.viewers} viewer{s.viewers !== 1 ? "s" : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Logs */}
      {logs && (
        <div className="bg-surface-container-low border border-outline-variant p-lg">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-code-label text-code-label uppercase tracking-widest text-on-surface-variant">
              Service Logs
            </h3>
            <button className="text-on-surface-variant text-[12px] hover:text-on-surface" onClick={() => setLogs("")}>
              Close
            </button>
          </div>
          <pre className="text-[11px] text-on-surface-variant bg-surface-container-lowest border border-outline-variant p-4 overflow-x-auto max-h-96 overflow-y-auto whitespace-pre-wrap font-mono">
            {logs}
          </pre>
        </div>
      )}
    </div>
  );
}

/* ── Bento stat card ─────────────────────────────────────────── */
function StatCard({ label, value, unit, sub, icon }: {
  label: string; value: number | string; unit?: string; sub?: string; icon: string;
}) {
  return (
    <div className="bg-surface-container-low border border-outline-variant p-md flex flex-col justify-between h-32 hover:bg-surface-container transition-colors">
      <span className="font-code-label text-code-label text-on-surface-variant uppercase tracking-tighter">{label}</span>
      <div className="flex items-end justify-between">
        <div className="flex flex-col min-w-0">
          <span className="font-display-lg text-display-lg leading-none">
            {value}{unit && <span className="text-xl font-normal opacity-40 ml-0.5">{unit}</span>}
          </span>
          {sub && <span className="font-code-label text-[10px] text-on-surface-variant mt-1">{sub}</span>}
        </div>
        <div className="w-10 h-10 flex items-center justify-center bg-surface-variant text-primary-fixed-dim shrink-0">
          <MIcon name={icon} fill size={22} />
        </div>
      </div>
    </div>
  );
}

/* ── Footer card ─────────────────────────────────────────────── */
function FooterCard({ label, value, icon }: { label: string; value: string; icon: string }) {
  return (
    <div className="bg-surface-container border border-outline-variant p-md flex items-center gap-md">
      <div className="w-10 h-10 flex items-center justify-center bg-surface-variant text-primary-fixed-dim shrink-0">
        <MIcon name={icon} size={22} />
      </div>
      <div>
        <p className="font-code-label text-[10px] uppercase text-on-surface-variant">{label}</p>
        <p className="text-headline-md font-headline-md font-bold leading-tight">{value}</p>
      </div>
    </div>
  );
}

/* ── Chart card ──────────────────────────────────────────────── */
function ChartCard({ label, dataKey, data }: { label: string; dataKey: string; data: any[] }) {
  return (
    <div className="bg-surface-container-low border border-outline-variant p-md">
      <div className="flex items-center gap-sm mb-md">
        <span className="w-2 h-2 rounded-full bg-primary-fixed-dim" />
        <h4 className="font-code-label text-code-label uppercase tracking-widest text-on-surface">{label}</h4>
      </div>
      <ResponsiveContainer width="100%" height={128}>
        <AreaChart data={data} margin={{ top: 4, right: 0, left: -30, bottom: 0 }}>
          <defs>
            <linearGradient id={`gs-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#c6c6c6" stopOpacity={0.2} />
              <stop offset="95%" stopColor="#c6c6c6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-outline-variant)" vertical={false} />
          <XAxis dataKey="t" hide />
          <YAxis tick={{ fontSize: 10, fill: "var(--color-on-surface-variant)" }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: "var(--color-surface-container)", border: "1px solid var(--color-outline-variant)", borderRadius: 0, fontSize: 11, color: "var(--color-on-surface)" }}
            labelStyle={{ color: "var(--color-on-surface-variant)" }}
          />
          <Area type="monotone" dataKey={dataKey} stroke="#c6c6c6" strokeWidth={1.5}
            fill={`url(#gs-${dataKey})`} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
