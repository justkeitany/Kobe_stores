import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Eye, EyeOff, Tv } from "lucide-react";
import { login } from "../lib/auth";
import toast from "react-hot-toast";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const data = await login(username, password);
      if (data.must_change_password) {
        // Redirect to forced password change before dashboard
        navigate("/change-password?forced=1");
      } else {
        navigate("/");
      }
    } catch (err: any) {
      const msg = err?.response?.data?.detail || "Invalid credentials";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex bg-white">
      {/* ── Left panel — form ─────────────────────────────────────── */}
      <div className="flex flex-col w-full lg:w-1/2 px-8 sm:px-16 justify-center">
        <div className="max-w-sm w-full mx-auto">
          <h1 className="text-3xl font-bold text-gray-900 mb-1">Sign In</h1>
          <p className="text-sm text-gray-500 mb-8">
            Enter your username and password to sign in.
          </p>

          {/* Default creds hint — only shown when fields are empty */}
          {!username && !password && (
            <div className="mb-6 px-4 py-3 bg-gray-50 border border-gray-200 rounded-[10px] text-xs text-gray-500">
              Default credentials:{" "}
              <span className="font-mono font-semibold text-gray-700">admin</span>{" "}
              /{" "}
              <span className="font-mono font-semibold text-gray-700">admin</span>
              <br />
              You will be asked to set a new username and password on first login.
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                Username <span className="text-gray-900">*</span>
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                autoComplete="username"
                required
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg text-sm
                           text-gray-900 placeholder-gray-400 bg-white
                           focus:outline-none focus:border-gray-900 focus:ring-1 focus:ring-gray-900
                           transition-colors"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                Password <span className="text-gray-900">*</span>
              </label>
              <div className="relative">
                <input
                  type={showPass ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  autoComplete="current-password"
                  required
                  className="w-full px-4 py-2.5 pr-11 border border-gray-300 rounded-lg text-sm
                             text-gray-900 placeholder-gray-400 bg-white
                             focus:outline-none focus:border-gray-900 focus:ring-1 focus:ring-gray-900
                             transition-colors"
                />
                <button
                  type="button"
                  onClick={() => setShowPass(!showPass)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700"
                  tabIndex={-1}
                >
                  {showPass ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 px-4 bg-gray-900 hover:bg-black text-white text-sm
                         font-semibold rounded-lg transition-colors disabled:opacity-50
                         disabled:cursor-not-allowed mt-2"
            >
              {loading ? "Signing in..." : "Sign In"}
            </button>
          </form>
        </div>
      </div>

      {/* ── Right panel — dark branding ───────────────────────────── */}
      <div className="hidden lg:flex flex-col items-center justify-center w-1/2
                      bg-gray-950 text-white px-16">
        <div className="w-16 h-16 bg-white rounded-2xl flex items-center justify-center mb-6">
          <Tv size={30} className="text-gray-900" />
        </div>
        <h2 className="text-2xl font-bold mb-3 text-center">IPTV Panel</h2>
        <p className="text-gray-400 text-sm text-center max-w-xs leading-relaxed">
          Personal IPTV dashboard — manage streams, categories, EPG, and monitor your server in real time.
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
