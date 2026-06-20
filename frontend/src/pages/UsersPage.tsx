import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Users, Key, Eye, EyeOff, RefreshCw, Copy } from "lucide-react";
import toast from "react-hot-toast";
import api, { xtreamBaseUrl } from "../lib/api";
import { copyToClipboard } from "../lib/clipboard";
import { MIcon } from "../components/MIcon";
import clsx from "clsx";

type UserTab = "all" | "active" | "expired";

function isUserExpired(u: { expires_at: string | null }) {
  return u.expires_at ? new Date(u.expires_at) < new Date() : false;
}

interface IUser {
  id: number;
  username: string;
  password: string;
  max_connections: number;
  expires_at: string | null;
  is_active: boolean;
  bouquet_id: number | null;
  notes: string | null;
  created_at: string;
}

interface Bouquet { id: number; name: string; }

export default function UsersPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<UserTab>("all");
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<IUser | null>(null);

  const { data: users = [], isLoading } = useQuery<IUser[]>({
    queryKey: ["users", search],
    queryFn: () => api.get("/users", { params: { search: search || undefined } }).then((r) => r.data),
    refetchInterval: 10000,
  });

  // Real-time concurrent figures (counts both HLS and .ts viewers), polled
  // independently of the user list so the tile reflects who is watching *now*.
  const { data: liveCounts } = useQuery<{ active_connections: number; active_streams: number }>({
    queryKey: ["live-connections"],
    queryFn: () => api.get("/server/connections").then((r) => r.data),
    refetchInterval: 5000,
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/users/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast.success("User deleted"); },
  });

  const toggleMut = useMutation({
    mutationFn: (id: number) => api.post(`/users/${id}/toggle`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });

  // Derived stats
  const total = users.length;
  const expiringSoon = users.filter((u) => {
    if (!u.expires_at || isUserExpired(u)) return false;
    const days = (new Date(u.expires_at).getTime() - Date.now()) / 86_400_000;
    return days <= 7;
  }).length;
  const disabled = users.filter((u) => !u.is_active).length;

  const visible = users.filter((u) => {
    if (tab === "active") return u.is_active && !isUserExpired(u);
    if (tab === "expired") return isUserExpired(u);
    return true;
  });

  const tabs: { key: UserTab; label: string }[] = [
    { key: "all", label: "All" },
    { key: "active", label: "Active" },
    { key: "expired", label: "Expired" },
  ];

  return (
    <div className="p-lg space-y-lg">
      <div className="flex items-end justify-between flex-wrap gap-md">
        <div>
          <h2 className="font-headline-md text-headline-md font-bold tracking-tight mb-1">Users Management</h2>
          <p className="text-on-surface-variant text-body-sm">Monitor and control individual stream access accounts.</p>
        </div>
        <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
          <MIcon name="person_add" size={20} /> Add User
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-gutter">
        <UserStat label="Total Users" value={total} />
        <UserStat label="Active Connections" value={liveCounts?.active_connections ?? 0} valueClass="text-green-500" />
        <UserStat label="Expiring Soon" value={expiringSoon} valueClass="text-orange-500" />
        <UserStat label="Disabled" value={disabled} valueClass="opacity-50" />
      </div>

      {/* Table */}
      <div className="bg-surface-container-low border border-outline-variant overflow-hidden">
        {/* Controls */}
        <div className="px-md py-sm border-b border-outline-variant flex items-center justify-between bg-surface-container gap-md flex-wrap">
          <div className="flex border border-outline-variant overflow-hidden rounded-md">
            {tabs.map((t, i) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={clsx(
                  "px-md py-xs font-code-label text-[12px] transition-colors",
                  i > 0 && "border-l border-outline-variant",
                  tab === t.key
                    ? "bg-surface-variant text-on-surface"
                    : "text-on-surface-variant hover:bg-surface-container-high"
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="relative w-full max-w-[20rem]">
            <MIcon name="filter_list" size={18}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
            <input
              className="input pl-10 py-1.5 text-[13px]"
              placeholder="Filter users..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-body-sm">
            <thead>
              <tr className="bg-surface-container/50 border-b border-outline-variant">
                <th className="px-md py-sm font-code-label text-[10px] uppercase text-on-surface-variant tracking-wider">Username</th>
                <th className="px-md py-sm font-code-label text-[10px] uppercase text-on-surface-variant tracking-wider">Password</th>
                <th className="px-md py-sm font-code-label text-[10px] uppercase text-on-surface-variant tracking-wider">Max Conn.</th>
                <th className="px-md py-sm font-code-label text-[10px] uppercase text-on-surface-variant tracking-wider">Expires</th>
                <th className="px-md py-sm font-code-label text-[10px] uppercase text-on-surface-variant tracking-wider text-center">Status</th>
                <th className="px-md py-sm font-code-label text-[10px] uppercase text-on-surface-variant tracking-wider">Xtream URL</th>
                <th className="px-md py-sm font-code-label text-[10px] uppercase text-on-surface-variant tracking-wider text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/30">
              {isLoading && (
                <tr><td colSpan={7} className="px-md py-8 text-center text-on-surface-variant">
                  <Loader2 size={18} className="animate-spin mx-auto" />
                </td></tr>
              )}
              {!isLoading && visible.length === 0 && (
                <tr><td colSpan={7} className="px-md py-10 text-center">
                  <Users size={28} className="mx-auto mb-2 text-on-surface-variant/40" />
                  <p className="text-on-surface-variant text-body-sm">
                    {users.length === 0
                      ? "No users yet. Add one to generate Xtream credentials."
                      : "No users match this filter."}
                  </p>
                </td></tr>
              )}
              {visible.map((u) => (
                <UserRow
                  key={u.id}
                  user={u}
                  onEdit={() => { setEditing(u); setShowModal(true); }}
                  onDelete={() => { if (confirm(`Delete user "${u.username}"?`)) deleteMut.mutate(u.id); }}
                  onToggle={() => toggleMut.mutate(u.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
        {!isLoading && users.length > 0 && (
          <div className="px-md py-3 bg-surface-container border-t border-outline-variant">
            <p className="text-on-surface-variant text-[12px]">
              Showing {visible.length.toLocaleString()} of {total.toLocaleString()} users
            </p>
          </div>
        )}
      </div>

      {showModal && (
        <UserModal
          user={editing}
          onClose={() => setShowModal(false)}
          onSaved={() => { setShowModal(false); qc.invalidateQueries({ queryKey: ["users"] }); }}
        />
      )}
    </div>
  );
}

function UserStat({ label, value, valueClass }: {
  label: string; value: number; valueClass?: string;
}) {
  return (
    <div className="bg-surface-container-low border border-outline-variant p-md">
      <p className="font-code-label text-[10px] uppercase text-on-surface-variant mb-base">{label}</p>
      <p className={clsx("text-headline-md font-headline-md font-bold", valueClass)}>{value.toLocaleString()}</p>
    </div>
  );
}

function useServerBase() {
  const { data } = useQuery({
    queryKey: ["server-url"],
    queryFn: async () => (await api.get("/settings")).data,
    staleTime: 60_000,
  });
  return xtreamBaseUrl(data?.server_url);
}

function UserRow({ user: u, onEdit, onDelete, onToggle }: {
  user: IUser;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
}) {
  const [showPass, setShowPass] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  
  const streamBaseHttp = useServerBase();

  async function copy(key: string, text: string) {
    const ok = await copyToClipboard(text);
    if (!ok) { toast.error("Copy failed"); return; }
    setCopiedKey(key);
    toast.success("Copied");
    setTimeout(() => setCopiedKey(null), 2000);
  }

  const xtreamUrl = `${streamBaseHttp}/player_api.php?username=${u.username}&password=${u.password}`;
  const m3uUrl = `${streamBaseHttp}/get.php?username=${u.username}&password=${u.password}&type=m3u_plus`;

  const isExpired = u.expires_at ? new Date(u.expires_at) < new Date() : false;
  const state = u.is_active && !isExpired ? "active" : isExpired ? "expired" : "suspended";
  const stateCfg = {
    active:    "border-green-900/50 bg-green-900/10 text-green-500",
    expired:   "border-red-900/50 bg-red-900/10 text-red-400",
    suspended: "border-outline-variant bg-surface-container text-on-surface-variant",
  } as const;

  return (
    <tr className="hover:bg-surface-container-high transition-colors group">
      <td className="px-md py-sm">
        <div className="flex items-center gap-sm">
          <MIcon name="person" size={20} className="text-on-surface-variant/50" />
          <span className="font-bold">{u.username}</span>
        </div>
      </td>
      <td className="px-md py-sm">
        <div className="flex items-center gap-sm font-code-label text-[14px]">
          <span className={clsx(!showPass && "tracking-[0.2em] opacity-40")}>
            {showPass ? u.password : "••••••••"}
          </span>
          <button onClick={() => setShowPass(!showPass)}
            className="text-on-surface-variant hover:text-primary-fixed-dim transition-colors">
            <MIcon name={showPass ? "visibility_off" : "visibility"} size={16} />
          </button>
          <button onClick={() => copy("pass-" + u.id, u.password)}
            className="text-on-surface-variant hover:text-primary-fixed-dim transition-colors">
            <MIcon name={copiedKey === "pass-" + u.id ? "check" : "content_copy"} size={16}
              className={copiedKey === "pass-" + u.id ? "text-green-400" : undefined} />
          </button>
        </div>
      </td>
      <td className="px-md py-sm font-code-label">{u.max_connections}</td>
      <td className="px-md py-sm font-code-label">
        {u.expires_at ? (
          <span className={isExpired ? "text-red-400" : undefined}>
            {new Date(u.expires_at).toLocaleDateString()}
          </span>
        ) : (
          <span className="text-on-surface-variant/50">Never</span>
        )}
      </td>
      <td className="px-md py-sm text-center">
        <button onClick={onToggle} title="Toggle active">
          <span className={clsx(
            "inline-flex items-center gap-xs px-sm py-[2px] border rounded-sm font-code-label text-[11px] uppercase",
            stateCfg[state]
          )}>
            <span className={clsx("w-1.5 h-1.5 rounded-full",
              state === "active" ? "bg-green-500" : state === "expired" ? "bg-red-400" : "bg-on-surface-variant")} />
            {state}
          </span>
        </button>
      </td>
      <td className="px-md py-sm">
        <div className="flex gap-sm">
          <button onClick={() => copy("xtream-" + u.id, xtreamUrl)} title="Copy Xtream API URL"
            className="flex items-center gap-xs px-sm py-[2px] border border-outline-variant hover:bg-surface-container-highest transition-colors font-code-label text-[11px]">
            <MIcon name={copiedKey === "xtream-" + u.id ? "check" : "link"} size={14}
              className={copiedKey === "xtream-" + u.id ? "text-green-400" : undefined} /> Xtream
          </button>
          <button onClick={() => copy("m3u-" + u.id, m3uUrl)} title="Copy M3U URL"
            className="flex items-center gap-xs px-sm py-[2px] border border-outline-variant hover:bg-surface-container-highest transition-colors font-code-label text-[11px]">
            <MIcon name={copiedKey === "m3u-" + u.id ? "check" : "description"} size={14}
              className={copiedKey === "m3u-" + u.id ? "text-green-400" : undefined} /> M3U
          </button>
        </div>
      </td>
      <td className="px-md py-sm text-right">
        <div className="flex items-center justify-end gap-sm opacity-40 group-hover:opacity-100 transition-opacity">
          <button title="Edit" className="p-1 hover:text-primary-fixed-dim transition-colors" onClick={onEdit}>
            <MIcon name="edit" size={18} />
          </button>
          <button title="Delete" className="p-1 hover:text-red-400 transition-colors" onClick={onDelete}>
            <MIcon name="delete" size={18} />
          </button>
        </div>
      </td>
    </tr>
  );
}

function UserModal({ user, onClose, onSaved }: {
  user: IUser | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [username, setUsername] = useState(user?.username ?? "");
  const [password, setPassword] = useState(user?.password ?? "");
  const [showPass, setShowPass] = useState(false);
  const [maxConn, setMaxConn] = useState(user?.max_connections ?? 1);
  const [expiresAt, setExpiresAt] = useState(
    user?.expires_at ? user.expires_at.split("T")[0] : ""
  );
  const [bouquetId, setBouquetId] = useState<number | "">(user?.bouquet_id ?? "");
  const [notes, setNotes] = useState(user?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const base = useServerBase();

  const { data: bouquets = [] } = useQuery<Bouquet[]>({
    queryKey: ["bouquets"],
    queryFn: () => api.get("/bouquets").then((r) => r.data),
  });

  

  function generatePassword() {
    const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
    const pwd = Array.from({ length: 12 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
    setPassword(pwd);
  }

  async function save() {
    setSaving(true);
    try {
      const payload = {
        username,
        password,
        max_connections: maxConn,
        expires_at: expiresAt || null,
        bouquet_id: bouquetId || null,
        notes: notes || null,
      };
      if (user) {
        await api.put(`/users/${user.id}`, payload);
      } else {
        await api.post("/users", payload);
      }
      toast.success(user ? "User updated" : "User created");
      onSaved();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-300 rounded-md w-full max-w-[28rem] p-6 space-y-4">
        <h2 className="text-lg font-semibold text-gray-900">
          {user ? "Edit User" : "Add User"}
        </h2>

        {/* Username */}
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Username *</label>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="e.g. john_doe"
            disabled={!!user}
          />
        </div>

        {/* Password */}
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Password *</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <input
                className="input pr-10"
                type={showPass ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Min. 6 characters"
              />
              <button
                type="button"
                onClick={() => setShowPass(!showPass)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700"
              >
                {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <button
              type="button"
              onClick={generatePassword}
              className="btn-secondary px-3 gap-1.5 shrink-0"
              title="Generate random password"
            >
              <RefreshCw size={13} />
              Generate
            </button>
          </div>
        </div>

        {/* Max connections + expiry */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">Max Connections</label>
            <input
              className="input"
              type="number"
              min={1}
              max={10}
              value={maxConn}
              onChange={(e) => setMaxConn(Number(e.target.value))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">Expiry Date</label>
            <input
              className="input"
              type="date"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
            />
          </div>
        </div>

        {/* Bouquet */}
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Bouquet (channel package)</label>
          <select
            className="input"
            value={bouquetId}
            onChange={(e) => setBouquetId(e.target.value ? Number(e.target.value) : "")}
          >
            <option value="">All channels</option>
            {bouquets.map((b) => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
        </div>

        {/* Notes */}
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Notes (optional)</label>
          <input
            className="input"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Family TV"
          />
        </div>

        {/* Xtream preview */}
        {username && password && (
          <div className="bg-gray-50 border border-gray-200 p-3 space-y-1.5">
            <p className="text-xs font-medium text-gray-600 mb-2 flex items-center gap-1.5">
              <Key size={12} /> Xtream Credentials Preview
            </p>
            <PreviewRow label="Server"   value={base} />
            <PreviewRow label="Username" value={username} />
            <PreviewRow label="Password" value={password} />
            <PreviewRow
              label="M3U URL"
              value={`${base}/get.php?username=${username}&password=${password}&type=m3u_plus`}
            />
          </div>
        )}

        <div className="flex gap-3 pt-1">
          <button className="btn-secondary flex-1 justify-center" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary flex-1 justify-center"
            onClick={save}
            disabled={saving || !username || !password}
          >
            {saving ? "Saving..." : user ? "Update User" : "Create User"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PreviewRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-gray-500 w-20 shrink-0">{label}</span>
      <code className="flex-1 text-gray-700 font-mono truncate">{value}</code>
      <button
        onClick={async () => {
          const ok = await copyToClipboard(value);
          ok ? toast.success("Copied") : toast.error("Copy failed");
        }}
        className="shrink-0 text-gray-400 hover:text-gray-700"
      >
        <Copy size={11} />
      </button>
    </div>
  );
}
