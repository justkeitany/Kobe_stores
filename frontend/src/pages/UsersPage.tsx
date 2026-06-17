import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Trash2, Edit2, Copy, Check, Loader2,
  Search, Users, Key, Eye, EyeOff, RefreshCw,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import clsx from "clsx";

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
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<IUser | null>(null);

  const { data: users = [], isLoading } = useQuery<IUser[]>({
    queryKey: ["users", search],
    queryFn: () => api.get("/users", { params: { search: search || undefined } }).then((r) => r.data),
    refetchInterval: 10000,
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/users/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast.success("User deleted"); },
  });

  const toggleMut = useMutation({
    mutationFn: (id: number) => api.post(`/users/${id}/toggle`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Users</h1>
        <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
          <Plus size={15} /> Add User
        </button>
      </div>

      {/* Search */}
      <div className="relative max-w-xs">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          className="input pl-9"
          placeholder="Search users..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Table */}
      <div className="card overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-200 bg-gray-50 text-xs">
                <th className="px-4 py-3 font-medium">Username</th>
                <th className="px-4 py-3 font-medium">Password</th>
                <th className="px-4 py-3 font-medium">Max Conn.</th>
                <th className="px-4 py-3 font-medium">Expires</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Xtream URL</th>
                <th className="px-4 py-3 font-medium w-28">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-400">
                  <Loader2 size={18} className="animate-spin mx-auto" />
                </td></tr>
              )}
              {!isLoading && users.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-10 text-center">
                  <Users size={28} className="mx-auto mb-2 text-gray-300" />
                  <p className="text-gray-400 text-sm">No users yet. Add one to generate Xtream credentials.</p>
                </td></tr>
              )}
              {users.map((u) => (
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

function useServerBase() {
  const { data } = useQuery({
    queryKey: ["server-url"],
    queryFn: async () => (await api.get("/settings")).data,
    staleTime: 60_000,
  });
  return ((data?.server_url || window.location.origin) as string).replace(/\/+$/, "");
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

  function copy(key: string, text: string) {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    toast.success("Copied");
    setTimeout(() => setCopiedKey(null), 2000);
  }

  const xtreamUrl = `${streamBaseHttp}/player_api.php?username=${u.username}&password=${u.password}`;
  const m3uUrl = `${streamBaseHttp}/get.php?username=${u.username}&password=${u.password}&type=m3u_plus`;

  const isExpired = u.expires_at ? new Date(u.expires_at) < new Date() : false;

  return (
    <tr className="border-b border-gray-100 table-row-hover">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-[10px] bg-gray-100 border border-gray-200 flex items-center justify-center">
            <Users size={12} className="text-gray-500" />
          </div>
          <span className="font-medium text-gray-900">{u.username}</span>
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1.5">
          <code className="text-xs text-gray-600 font-mono">
            {showPass ? u.password : "••••••••"}
          </code>
          <button
            onClick={() => setShowPass(!showPass)}
            className="p-1 text-gray-300 hover:text-gray-600 transition-colors"
          >
            {showPass ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
          <button
            onClick={() => copy("pass-" + u.id, u.password)}
            className="p-1 text-gray-300 hover:text-gray-600 transition-colors"
          >
            {copiedKey === "pass-" + u.id ? <Check size={12} className="text-green-600" /> : <Copy size={12} />}
          </button>
        </div>
      </td>
      <td className="px-4 py-3 text-gray-600">{u.max_connections}</td>
      <td className="px-4 py-3">
        {u.expires_at ? (
          <span className={clsx("text-xs", isExpired ? "text-red-600 font-medium" : "text-gray-600")}>
            {new Date(u.expires_at).toLocaleDateString()}
            {isExpired && " (expired)"}
          </span>
        ) : (
          <span className="text-gray-400 text-xs">Never</span>
        )}
      </td>
      <td className="px-4 py-3">
        <button onClick={onToggle}>
          <span className={clsx(
            "text-xs font-medium px-2 py-0.5 rounded-full border",
            u.is_active && !isExpired
              ? "bg-green-50 text-green-700 border-green-200"
              : "bg-gray-100 text-gray-500 border-gray-200"
          )}>
            {u.is_active && !isExpired ? "Active" : isExpired ? "Expired" : "Suspended"}
          </span>
        </button>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1">
          <button
            onClick={() => copy("xtream-" + u.id, xtreamUrl)}
            title="Copy Xtream API URL"
            className="flex items-center gap-1 px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-[10px] border border-gray-200 transition-colors"
          >
            <Key size={11} />
            Xtream
            {copiedKey === "xtream-" + u.id ? <Check size={11} className="text-green-600" /> : <Copy size={11} />}
          </button>
          <button
            onClick={() => copy("m3u-" + u.id, m3uUrl)}
            title="Copy M3U URL"
            className="flex items-center gap-1 px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-[10px] border border-gray-200 transition-colors"
          >
            M3U
            {copiedKey === "m3u-" + u.id ? <Check size={11} className="text-green-600" /> : <Copy size={11} />}
          </button>
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1">
          <button
            title="Edit"
            className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
            onClick={onEdit}
          >
            <Edit2 size={14} />
          </button>
          <button
            title="Delete"
            className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
            onClick={onDelete}
          >
            <Trash2 size={14} />
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
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-200 rounded-[10px] w-full max-w-md shadow-xl p-6 space-y-4">
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
          <div className="bg-gray-50 border border-gray-200 rounded-[10px] p-3 space-y-1.5">
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
        onClick={() => { navigator.clipboard.writeText(value); toast.success("Copied"); }}
        className="shrink-0 text-gray-400 hover:text-gray-700"
      >
        <Copy size={11} />
      </button>
    </div>
  );
}
