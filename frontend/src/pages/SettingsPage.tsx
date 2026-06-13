import { useState, useEffect } from "react";
import { Settings, Save, Copy, Link, KeyRound, Eye, EyeOff } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";

export default function SettingsPage() {
  // ── Server / streaming settings ────────────────────────────
  const [serverUrl, setServerUrl]         = useState("");
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

  useEffect(() => {
    api.get("/settings").then((r) => {
      const s = r.data;
      setServerUrl(s.server_url || window.location.origin);
      setHlsSegmentTime(s.hls_segment_time || "2");
      setHlsListSize(s.hls_list_size || "6");
      setMaxRetry(s.max_retry || "5");
      setHealthCheck(s.health_check || "30");
    }).catch(() => setServerUrl(window.location.origin));
  }, []);

  async function saveSettings() {
    setSavingSettings(true);
    try {
      await api.put("/settings/bulk", {
        server_url: serverUrl,
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

  function copy(text: string) {
    navigator.clipboard.writeText(text);
    toast.success("Copied");
  }

  const base = "https://live.keitanyfrank.store";

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Settings</h1>

      {/* ── Server URL ──────────────────────────────────────── */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Settings size={14} className="text-gray-400" /> Server Configuration
        </h2>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Public Server URL</label>
          <input
            className="input"
            value={serverUrl}
            onChange={(e) => setServerUrl(e.target.value)}
            placeholder="https://live.keitanyfrank.store"
          />
          <p className="text-xs text-gray-400 mt-1">This URL is embedded in all M3U playlists and Xtream links.</p>
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
