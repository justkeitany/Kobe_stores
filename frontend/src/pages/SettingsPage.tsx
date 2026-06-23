import { useState, useEffect } from "react";
import {
  Settings, Save, Copy, Link, KeyRound, Eye, EyeOff,
  Globe, Server as ServerIcon, Loader2, CheckCircle2, AlertCircle,
  Shield, RotateCcw,
} from "lucide-react";
import toast from "react-hot-toast";
import api, { xtreamBaseUrl } from "../lib/api";
import { copyToClipboard } from "../lib/clipboard";

type SslStatus = "none" | "pending" | "active" | "failed";

export default function SettingsPage() {
  // ── Access mode (IP vs domain) ─────────────────────────────
  const [mode, setMode]                   = useState<"ip" | "domain">("ip");
  const [domainInput, setDomainInput]     = useState("");
  const [serverUrl, setServerUrl]         = useState(window.location.origin);
  const [sslStatus, setSslStatus]         = useState<SslStatus>("none");
  const [sslMessage, setSslMessage]       = useState("");
  const [savingDomain, setSavingDomain]   = useState(false);

  // ── FFmpeg / streaming settings ────────────────────────────
  const [hlsSegmentTime, setHlsSegmentTime] = useState("2");
  const [hlsListSize, setHlsListSize]     = useState("6");
  const [maxRetry, setMaxRetry]           = useState("5");
  const [healthCheck, setHealthCheck]     = useState("30");
  const [savingSettings, setSavingSettings] = useState(false);

  // ── Change password ─────────────────────────────────────────
  const [currentPass, setCurrentPass]   = useState("");
  const [newPass, setNewPass]           = useState("");
  const [confirmPass, setConfirmPass]   = useState("");
  const [showCurrent, setShowCurrent]   = useState(false);
  const [showNew, setShowNew]           = useState(false);
  const [savingPass, setSavingPass]     = useState(false);

  // ── Proxy pool ───────────────────────────────────────────────
  const [proxyRaw, setProxyRaw]         = useState("");
  const [proxyGeo, setProxyGeo]         = useState<any[]>([]);
  const [proxyCount, setProxyCount]     = useState(0);
  const [proxyBwUsed, setProxyBwUsed]   = useState(0);
  const [proxyBwQuota, setProxyBwQuota] = useState(0);
  const [savingProxy, setSavingProxy]   = useState(false);
  const [resettingBw, setResettingBw]   = useState(false);

  function applyDomainData(d: any) {
    setMode(d.mode === "domain" ? "domain" : "ip");
    setDomainInput(d.domain || "");
    setServerUrl(d.server_url || window.location.origin);
    setSslStatus((d.ssl_status as SslStatus) || "none");
    setSslMessage(d.ssl_message || "");
  }

  useEffect(() => {
    api.get("/domain").then((r) => applyDomainData(r.data)).catch(() => {});
    api.get("/settings").then((r) => {
      const s = r.data;
      setHlsSegmentTime(s.hls_segment_time || "2");
      setHlsListSize(s.hls_list_size || "6");
      setMaxRetry(s.max_retry || "5");
      setHealthCheck(s.health_check || "30");
    }).catch(() => {});
    api.get("/settings/proxy/pool").then((r) => {
      setProxyRaw(r.data.raw || "");
      setProxyGeo(r.data.geo || []);
      setProxyCount(r.data.count || 0);
      setProxyBwUsed(r.data.bandwidth_used || 0);
      setProxyBwQuota(r.data.bandwidth_quota || 0);
    }).catch(() => {});
  }, []);

  // Poll while a certificate is being issued.
  useEffect(() => {
    if (sslStatus !== "pending") return;
    const id = setInterval(() => {
      api.get("/domain").then((r) => {
        applyDomainData(r.data);
        if (r.data.ssl_status === "active") toast.success("HTTPS enabled");
        if (r.data.ssl_status === "failed") toast.error("HTTPS setup failed");
      }).catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [sslStatus]);

  async function saveDomain(nextMode: "ip" | "domain") {
    if (nextMode === "domain" && !domainInput.trim()) {
      toast.error("Enter your domain first");
      return;
    }
    setSavingDomain(true);
    try {
      const body = nextMode === "domain"
        ? { mode: "domain", domain: domainInput.trim().toLowerCase() }
        : { mode: "ip" };
      const r = await api.post("/domain", body);
      applyDomainData({ ...r.data, ssl_message: r.data.ssl_status === "pending" ? "Issuing certificate…" : "" });
      setMode(nextMode);
      if (nextMode === "domain") toast.success("Domain saved — issuing HTTPS certificate…");
      else toast.success("Switched to server IP");
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to update access mode");
    } finally {
      setSavingDomain(false);
    }
  }

  async function saveSettings() {
    setSavingSettings(true);
    try {
      await api.put("/settings/bulk", {
        hls_segment_time: hlsSegmentTime,
        hls_list_size: hlsListSize,
        max_retry: maxRetry,
        health_check: healthCheck,
      });
      toast.success("Settings saved");
    } catch {
      toast.error("Failed to save settings");
    } finally {
      setSavingSettings(false);
    }
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    if (newPass !== confirmPass) { toast.error("Passwords do not match"); return; }
    if (newPass.length < 8)      { toast.error("Password must be at least 8 characters"); return; }
    setSavingPass(true);
    try {
      await api.post("/auth/change-password", {
        current_password: currentPass,
        new_password: newPass,
      });
      toast.success("Password updated");
      setCurrentPass(""); setNewPass(""); setConfirmPass("");
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to update password");
    } finally {
      setSavingPass(false);
    }
  }

  async function copy(text: string) {
    const ok = await copyToClipboard(text);
    ok ? toast.success("Copied") : toast.error("Copy failed");
  }

  async function saveProxy() {
    setSavingProxy(true);
    try {
      const r = await api.put("/settings/proxy/pool", { raw: proxyRaw });
      setProxyGeo(r.data.geo || []);
      setProxyCount(r.data.count || 0);
      toast.success(`${r.data.count} proxy(s) saved & geo-detected`);
    } catch {
      toast.error("Failed to save proxy pool");
    } finally {
      setSavingProxy(false);
    }
  }

  async function resetProxyBw() {
    setResettingBw(true);
    try {
      await api.post("/settings/proxy/reset", { action: "reset_bandwidth" });
      setProxyBwUsed(0);
      toast.success("Proxy bandwidth counter reset");
    } catch {
      toast.error("Failed to reset");
    } finally {
      setResettingBw(false);
    }
  }

  // Xtream links use the dedicated player port (8080) in IP mode, or the domain if set.
  const base = xtreamBaseUrl(mode === "domain" ? serverUrl : "");

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Settings</h1>

      {/* ── Access mode ─────────────────────────────────────── */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Settings size={14} className="text-gray-400" /> Access &amp; Domain
        </h2>
        <p className="text-xs text-gray-400">
          Choose how your panel and Xtream links are addressed. This URL is embedded in
          all M3U playlists and Xtream links.
        </p>

        {/* Option: server IP */}
        <button
          type="button"
          onClick={() => mode !== "ip" && saveDomain("ip")}
          disabled={savingDomain}
          className={`w-full text-left flex items-start gap-3 p-3 rounded-[10px] border transition-colors ${
            mode === "ip" ? "border-gray-900 bg-gray-50" : "border-gray-200 hover:border-gray-300"
          }`}
        >
          <ServerIcon size={16} className="text-gray-500 mt-0.5" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-900">Use server IP</p>
            <p className="text-xs text-gray-500 break-all">{window.location.origin}</p>
          </div>
          {mode === "ip" && <CheckCircle2 size={16} className="text-gray-900 ml-auto" />}
        </button>

        {/* Option: custom domain */}
        <div
          className={`p-3 rounded-[10px] border ${
            mode === "domain" ? "border-gray-900 bg-gray-50" : "border-gray-200"
          }`}
        >
          <div className="flex items-start gap-3">
            <Globe size={16} className="text-gray-500 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-900">Use my domain</p>
              <p className="text-xs text-gray-500 mb-2">
                Point your domain's DNS (A record) at this server, then enter it below.
                HTTPS is set up automatically.
              </p>
              <div className="flex gap-2">
                <input
                  className="input"
                  value={domainInput}
                  onChange={(e) => setDomainInput(e.target.value)}
                  placeholder="tv.example.com"
                />
                <button
                  className="btn-primary px-3 whitespace-nowrap"
                  onClick={() => saveDomain("domain")}
                  disabled={savingDomain}
                >
                  {savingDomain ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                  Save &amp; enable HTTPS
                </button>
              </div>

              {/* SSL status */}
              {mode === "domain" && sslStatus === "pending" && (
                <p className="text-xs text-amber-600 mt-2 flex items-center gap-1.5">
                  <Loader2 size={12} className="animate-spin" /> Issuing HTTPS certificate… this can take a minute.
                </p>
              )}
              {mode === "domain" && sslStatus === "active" && (
                <p className="text-xs text-green-600 mt-2 flex items-center gap-1.5">
                  <CheckCircle2 size={12} /> HTTPS active — {serverUrl}
                </p>
              )}
              {mode === "domain" && sslStatus === "failed" && (
                <p className="text-xs text-red-500 mt-2 flex items-start gap-1.5">
                  <AlertCircle size={12} className="mt-0.5 shrink-0" />
                  <span>
                    HTTPS failed: {sslMessage || "check that the domain's DNS points to this server, then try again."}
                    {" "}The panel still works over <span className="font-mono">http://{domainInput}</span>.
                  </span>
                </p>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── FFmpeg / Streaming ───────────────────────────────── */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-gray-900">FFmpeg / Streaming</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">HLS Segment Time (s)</label>
            <input className="input" type="number" min={1} max={10} value={hlsSegmentTime}
              onChange={(e) => setHlsSegmentTime(e.target.value)} />
            <p className="text-xs text-gray-400 mt-1">Lower = less latency. 2s recommended.</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">HLS List Size</label>
            <input className="input" type="number" min={2} max={20} value={hlsListSize}
              onChange={(e) => setHlsListSize(e.target.value)} />
            <p className="text-xs text-gray-400 mt-1">Segments in playlist. 6 = ~12s buffer.</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">Max Retry Attempts</label>
            <input className="input" type="number" min={1} value={maxRetry}
              onChange={(e) => setMaxRetry(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">Health Check Interval (s)</label>
            <input className="input" type="number" min={10} value={healthCheck}
              onChange={(e) => setHealthCheck(e.target.value)} />
          </div>
        </div>
        <button className="btn-primary w-fit" onClick={saveSettings} disabled={savingSettings}>
          <Save size={13} /> {savingSettings ? "Saving..." : "Save Settings"}
        </button>
      </div>

      {/* ── Xtream Endpoints ─────────────────────────────────── */}
      <div className="card space-y-3">
        <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Link size={14} className="text-gray-400" /> Xtream Codes API Endpoints
        </h2>
        <p className="text-xs text-gray-400">
          Use these in TiviMate, IPTV Smarters, GSE, VLC, or any Xtream-compatible player.
        </p>
        <EndpointRow label="Server URL"   value={base} onCopy={copy} />
        <EndpointRow label="M3U Playlist" value={`${base}/get.php?username=admin&password=YOUR_PASS&type=m3u_plus`} onCopy={copy} />
        <EndpointRow label="Player API"   value={`${base}/player_api.php?username=admin&password=YOUR_PASS`} onCopy={copy} />
        <EndpointRow label="XMLTV EPG"    value={`${base}/xmltv.php?username=admin&password=YOUR_PASS`} onCopy={copy} />
        <EndpointRow label="Live Stream"  value={`${base}/live/admin/YOUR_PASS/{stream_id}.m3u8`} onCopy={copy} />
      </div>

      {/* ── Proxy Pool ────────────────────────────────────────── */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Shield size={14} className="text-gray-400" /> Residential Proxy Pool
        </h2>
        <p className="text-xs text-gray-400">
          One <code>host:port:user:pass</code> per line (user:pass optional).
          Used ONLY to resolve geo-blocked M3U8 playlists; segments flow direct.
        </p>
        <textarea
          className="input font-mono text-xs h-32 resize-y"
          value={proxyRaw}
          onChange={(e) => setProxyRaw(e.target.value)}
          placeholder={`31.59.20.176:6754:xkoaapub:eac08smczt5p\n31.56.127.193:7684:xkoaapub:eac08smczt5p`}
        />
        {proxyGeo.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-gray-600">Geo-detected ({proxyCount} proxies)</p>
            <div className="flex flex-wrap gap-1.5">
              {proxyGeo.map((p: any, i: number) => (
                <span key={i} className="inline-flex items-center gap-1 text-[11px] bg-surface-container border border-outline-variant rounded-full px-2 py-0.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                  {p.host}:{p.port}{' '}
                  <span className="text-on-surface-variant/60">[{p.country || '??'}]</span>
                </span>
              ))}
            </div>
          </div>
        )}
        <div className="flex items-center gap-3 flex-wrap">
          <button className="btn-primary" onClick={saveProxy} disabled={savingProxy}>
            <Save size={13} /> {savingProxy ? "Saving…" : "Save Proxies"}
          </button>
          <span className="text-xs text-on-surface-variant">
            Proxy data: {proxyBwUsed > 1024 * 1024
              ? `${(proxyBwUsed / 1024 / 1024).toFixed(1)} MB`
              : `${(proxyBwUsed / 1024).toFixed(0)} KB`}{' '}
            of {proxyBwQuota > 1024 * 1024 * 1024
              ? `${(proxyBwQuota / 1024 / 1024 / 1024).toFixed(1)} GB`
              : `${(proxyBwQuota / 1024 / 1024).toFixed(0)} MB`}
          </span>
          <button className="btn-secondary text-[11px] px-2 py-1" onClick={resetProxyBw} disabled={resettingBw}>
            <RotateCcw size={11} /> {resettingBw ? "…" : "Reset counter"}
          </button>
        </div>
      </div>

      {/* ── Change Password ───────────────────────────────────── */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <KeyRound size={14} className="text-gray-400" /> Change Password
        </h2>
        <p className="text-xs text-gray-400">
          To change your username, use the{" "}
          <a href="/change-password" className="text-gray-700 underline underline-offset-2">
            Set Credentials
          </a>{" "}
          page (logs you out).
        </p>
        <form onSubmit={changePassword} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">Current Password *</label>
            <div className="relative">
              <input
                type={showCurrent ? "text" : "password"}
                value={currentPass}
                onChange={(e) => setCurrentPass(e.target.value)}
                placeholder="Enter current password"
                required
                className="input pr-10"
              />
              <button type="button" tabIndex={-1}
                onClick={() => setShowCurrent(!showCurrent)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700">
                {showCurrent ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1.5">New Password *</label>
              <div className="relative">
                <input
                  type={showNew ? "text" : "password"}
                  value={newPass}
                  onChange={(e) => setNewPass(e.target.value)}
                  placeholder="Min. 8 characters"
                  required
                  className="input pr-10"
                />
                <button type="button" tabIndex={-1}
                  onClick={() => setShowNew(!showNew)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700">
                  {showNew ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1.5">Confirm Password *</label>
              <input
                type="password"
                value={confirmPass}
                onChange={(e) => setConfirmPass(e.target.value)}
                placeholder="Repeat new password"
                required
                className={`input ${confirmPass && confirmPass !== newPass ? "border-red-300 focus:border-red-400 focus:ring-red-300" : ""}`}
              />
              {confirmPass && confirmPass !== newPass && (
                <p className="text-xs text-red-500 mt-1">Passwords don't match</p>
              )}
            </div>
          </div>

          <button
            type="submit"
            disabled={savingPass || !currentPass || !newPass || newPass !== confirmPass || newPass.length < 8}
            className="btn-primary w-fit"
          >
            <KeyRound size={13} /> {savingPass ? "Updating..." : "Update Password"}
          </button>
        </form>
      </div>
    </div>
  );
}

function EndpointRow({ label, value, onCopy }: {
  label: string; value: string; onCopy: (v: string) => void;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-500 mb-1">{label}</label>
      <div className="flex gap-2">
        <code className="flex-1 bg-gray-50 border border-gray-200 text-gray-700 text-xs px-3 py-2 rounded-[10px] truncate font-mono">
          {value}
        </code>
        <button className="btn-secondary px-3" onClick={() => onCopy(value)}>
          <Copy size={12} />
        </button>
      </div>
    </div>
  );
}
