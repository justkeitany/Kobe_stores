import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Eye, EyeOff, ShieldCheck, Tv } from "lucide-react";
import api from "../lib/api";
import toast from "react-hot-toast";

export default function ChangePassword() {
  const [searchParams] = useSearchParams();
  const forced = searchParams.get("forced") === "1";
  const navigate = useNavigate();

  const [currentPass, setCurrentPass]   = useState("");
  const [newUsername, setNewUsername]   = useState("");
  const [newPass, setNewPass]           = useState("");
  const [confirmPass, setConfirmPass]   = useState("");
  const [showCurrent, setShowCurrent]   = useState(false);
  const [showNew, setShowNew]           = useState(false);
  const [saving, setSaving]             = useState(false);

  const strength = getStrength(newPass);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (newPass !== confirmPass) { toast.error("Passwords do not match"); return; }
    if (newPass.length < 8)      { toast.error("Password must be at least 8 characters"); return; }
    if (newUsername.trim().length < 3) { toast.error("Username must be at least 3 characters"); return; }

    setSaving(true);
    try {
      await api.post("/auth/change-credentials", {
        current_password: currentPass,
        new_username: newUsername.trim(),
        new_password: newPass,
      });
      toast.success("Credentials updated — please sign in again");
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
      navigate("/login");
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to update credentials");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen flex bg-white">
      {/* ── Left panel ── */}
      <div className="flex flex-col w-full lg:w-1/2 px-8 sm:px-16 justify-center">
        <div className="max-w-sm w-full mx-auto">
          <div className="w-12 h-12 bg-gray-900 rounded-[10px] flex items-center justify-center mb-6">
            <ShieldCheck size={22} className="text-white" />
          </div>

          <h1 className="text-3xl font-bold text-gray-900 mb-1">Set Your Credentials</h1>
          <p className="text-sm text-gray-500 mb-6">
            {forced
              ? "You're using the default credentials. Choose a unique username and a strong password."
              : "Update your admin username and password."}
          </p>

          {forced && (
            <div className="mb-6 px-4 py-3 bg-amber-50 border border-amber-200 rounded-[10px] text-xs text-amber-700">
              For security, you must change the default <strong>admin / admin</strong> credentials before accessing the dashboard.
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Current password */}
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">
                Current Password <span className="text-gray-900">*</span>
              </label>
              <div className="relative">
                <input
                  type={showCurrent ? "text" : "password"}
                  value={currentPass}
                  onChange={(e) => setCurrentPass(e.target.value)}
                  placeholder={forced ? "admin" : "Current password"}
                  required
                  className="w-full px-4 py-2.5 pr-11 border border-gray-300 rounded-[10px] text-sm
                             text-gray-900 placeholder-gray-400 bg-white
                             focus:outline-none focus:border-gray-900 focus:ring-1 focus:ring-gray-900 transition-colors"
                />
                <button type="button" tabIndex={-1}
                  onClick={() => setShowCurrent(!showCurrent)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700">
                  {showCurrent ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {/* New username */}
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">
                New Username <span className="text-gray-900">*</span>
              </label>
              <input
                type="text"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                placeholder="e.g. keitany"
                required
                className="w-full px-4 py-2.5 border border-gray-300 rounded-[10px] text-sm
                           text-gray-900 placeholder-gray-400 bg-white
                           focus:outline-none focus:border-gray-900 focus:ring-1 focus:ring-gray-900 transition-colors"
              />
              <p className="text-xs text-gray-400 mt-1">Letters, numbers, - _ . only. Min 3 characters.</p>
            </div>

            {/* New password */}
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">
                New Password <span className="text-gray-900">*</span>
              </label>
              <div className="relative">
                <input
                  type={showNew ? "text" : "password"}
                  value={newPass}
                  onChange={(e) => setNewPass(e.target.value)}
                  placeholder="Min. 8 characters"
                  required
                  className="w-full px-4 py-2.5 pr-11 border border-gray-300 rounded-[10px] text-sm
                             text-gray-900 placeholder-gray-400 bg-white
                             focus:outline-none focus:border-gray-900 focus:ring-1 focus:ring-gray-900 transition-colors"
                />
                <button type="button" tabIndex={-1}
                  onClick={() => setShowNew(!showNew)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700">
                  {showNew ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              {newPass.length > 0 && (
                <div className="mt-2 space-y-1">
                  <div className="flex gap-1">
                    {[1, 2, 3, 4].map((i) => (
                      <div key={i} className={`h-1 flex-1 rounded-full transition-colors ${
                        i <= strength.score
                          ? strength.score <= 1 ? "bg-red-400"
                          : strength.score === 2 ? "bg-amber-400"
                          : strength.score === 3 ? "bg-yellow-400"
                          : "bg-green-500"
                          : "bg-gray-200"
                      }`} />
                    ))}
                  </div>
                  <p className="text-xs text-gray-400">{strength.label}</p>
                </div>
              )}
            </div>

            {/* Confirm password */}
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">
                Confirm Password <span className="text-gray-900">*</span>
              </label>
              <input
                type="password"
                value={confirmPass}
                onChange={(e) => setConfirmPass(e.target.value)}
                placeholder="Repeat new password"
                required
                className={`w-full px-4 py-2.5 border rounded-[10px] text-sm text-gray-900 placeholder-gray-400 bg-white
                           focus:outline-none focus:ring-1 transition-colors
                           ${confirmPass && confirmPass !== newPass
                             ? "border-red-300 focus:border-red-400 focus:ring-red-300"
                             : "border-gray-300 focus:border-gray-900 focus:ring-gray-900"}`}
              />
              {confirmPass && confirmPass !== newPass && (
                <p className="text-xs text-red-500 mt-1">Passwords do not match</p>
              )}
            </div>

            <button
              type="submit"
              disabled={saving || newPass !== confirmPass || newPass.length < 8 || newUsername.trim().length < 3}
              className="w-full py-2.5 px-4 bg-gray-900 hover:bg-black text-white text-sm
                         font-semibold rounded-[10px] transition-colors disabled:opacity-40
                         disabled:cursor-not-allowed mt-2"
            >
              {saving ? "Saving..." : "Save Credentials"}
            </button>

            {!forced && (
              <button type="button" onClick={() => navigate("/settings")}
                className="w-full py-2 text-sm text-gray-400 hover:text-gray-700 transition-colors">
                Cancel
              </button>
            )}
          </form>
        </div>
      </div>

      {/* ── Right panel ── */}
      <div className="hidden lg:flex flex-col items-center justify-center w-1/2 bg-gray-950 text-white px-16">
        <div className="w-16 h-16 bg-white rounded-[10px] flex items-center justify-center mb-6">
          <Tv size={30} className="text-gray-900" />
        </div>
        <h2 className="text-2xl font-bold mb-3 text-center">IPTV Panel</h2>
        <p className="text-gray-400 text-sm text-center max-w-xs leading-relaxed">
          Set a unique username and strong password to secure your panel.
        </p>
        <div className="mt-12 grid grid-cols-6 gap-3 opacity-20">
          {Array.from({ length: 36 }).map((_, i) => (
            <div key={i} className="w-1.5 h-1.5 rounded-full bg-white" />
          ))}
        </div>
      </div>
    </div>
  );
}

function getStrength(pass: string): { score: number; label: string } {
  let score = 0;
  if (pass.length >= 8) score++;
  if (pass.length >= 12) score++;
  if (/[A-Z]/.test(pass) && /[a-z]/.test(pass)) score++;
  if (/[0-9]/.test(pass) && /[^A-Za-z0-9]/.test(pass)) score++;
  return { score, label: ["", "Weak", "Fair", "Good", "Strong"][score] || "" };
}
