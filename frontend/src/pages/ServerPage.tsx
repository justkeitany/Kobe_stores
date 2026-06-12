import { useEffect, useState } from "react";
import { Cpu, MemoryStick, Wifi, Activity, StopCircle, FileText } from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { useServerStats } from "../hooks/useServerStats";
import api from "../lib/api";
import toast from "react-hot-toast";

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

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Server Monitor</h1>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={fetchLogs} disabled={loadingLogs}>
            <FileText size={14} /> View Logs
          </button>
          <button className="btn-danger" onClick={restartAll}>
            <StopCircle size={14} /> Restart All Streams
          </button>
        </div>
      </div>

      {/* Live stats cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="CPU" value={`${stats?.cpu_percent ?? 0}%`} icon={<Cpu size={16} className="text-blue-400" />} />
        <StatCard label="RAM" value={`${stats?.ram_percent ?? 0}%`}
          sub={stats ? `${stats.ram_used_mb}/${stats.ram_total_mb} MB` : undefined}
          icon={<MemoryStick size={16} className="text-purple-400" />} />
        <StatCard label="Bandwidth Out" value={`${stats?.bw_out_kbps ?? 0} kbps`}
          icon={<Wifi size={16} className="text-green-400" />} />
        <StatCard label="Active Streams" value={stats?.active_streams ?? 0}
          icon={<Activity size={16} className="text-orange-400" />} />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard label="CPU %" dataKey="cpu" color="#3b82f6" data={history} />
        <ChartCard label="RAM %" dataKey="ram" color="#8b5cf6" data={history} />
        <ChartCard label="Bandwidth Out (kbps)" dataKey="bw" color="#f97316" data={history} />
      </div>

      {/* Active FFmpeg processes */}
      {stats && stats.streams.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">FFmpeg Processes</h2>
          <div className="space-y-2">
            {stats.streams.map((s: any) => (
              <div key={s.id} className="flex items-center justify-between px-3 py-2 bg-gray-800 rounded-lg text-sm">
                <span className="text-gray-300">Stream #{s.id}</span>
                <span className={`text-xs font-medium ${
                  s.status === "running" ? "text-green-400" :
                  s.status === "error" ? "text-red-400" :
                  "text-gray-400"
                }`}>{s.status}</span>
                <span className="text-gray-500 text-xs">{s.viewers} viewer{s.viewers !== 1 ? "s" : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Logs */}
      {logs && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-300">Service Logs</h2>
            <button className="text-gray-500 text-xs hover:text-gray-300" onClick={() => setLogs("")}>
              Close
            </button>
          </div>
          <pre className="text-xs text-gray-400 bg-gray-950 rounded-lg p-4 overflow-x-auto max-h-96 overflow-y-auto whitespace-pre-wrap">
            {logs}
          </pre>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, sub, icon }: {
  label: string; value: string | number; sub?: string; icon: React.ReactNode;
}) {
  return (
    <div className="card">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-gray-500 mb-1">{label}</p>
          <p className="text-xl font-bold text-white">{value}</p>
          {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
        </div>
        <div className="p-2 bg-gray-800 rounded-lg">{icon}</div>
      </div>
    </div>
  );
}

function ChartCard({ label, dataKey, color, data }: {
  label: string; dataKey: string; color: string; data: any[];
}) {
  return (
    <div className="card">
      <p className="text-xs text-gray-500 mb-3">{label}</p>
      <ResponsiveContainer width="100%" height={100}>
        <AreaChart data={data} margin={{ top: 0, right: 0, left: -30, bottom: 0 }}>
          <defs>
            <linearGradient id={`g-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.3} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
          <XAxis dataKey="t" hide />
          <YAxis tick={{ fontSize: 10, fill: "#6b7280" }} />
          <Tooltip
            contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 11 }}
          />
          <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2}
            fill={`url(#g-${dataKey})`} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
