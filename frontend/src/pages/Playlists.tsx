import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Copy, RefreshCw, Trash2, Eye, Loader2, Globe, X, AlertCircle, Download, Check,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { MIcon } from "../components/MIcon";
import { copyToClipboard } from "../lib/clipboard";
import clsx from "clsx";

interface Playlist {
  id: number;
  name: string;
  url: string;
  description: string | null;
  channel_count: number;
  logos: string[];
  health: string | null;
  last_refreshed: string | null;
  last_error: string | null;
  created_at: string;
}

// Compact "3h ago" style relative time for the last-checked line.
function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 90) return "just now";
  const mins = secs / 60;
  if (mins < 60) return `${Math.round(mins)}m ago`;
  const hrs = mins / 60;
  if (hrs < 24) return `${Math.round(hrs)}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// Parse a "alive/sampled live" health string so we can colour it.
function healthTone(health: string | null, hasError: boolean): "good" | "warn" | "muted" {
  if (hasError) return "warn";
  const m = health?.match(/^(\d+)\/(\d+)/);
  if (m) {
    const [a, b] = [Number(m[1]), Number(m[2])];
    if (b > 0 && a === b) return "good";
    if (b > 0) return a > b / 2 ? "good" : "warn";
  }
  return "muted";
}

interface PlaylistChannel {
  id: string;
  name: string;
  category: string;
  logo: string;
  url: string;
}

export default function Playlists() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [viewing, setViewing] = useState<Playlist | null>(null);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);

  const { data: playlists = [], isLoading } = useQuery<Playlist[]>({
    queryKey: ["playlists"],
    queryFn: () => api.get("/playlists").then((r) => r.data),
  });

  const refreshMut = useMutation({
    mutationFn: (id: number) => api.post(`/playlists/${id}/refresh`).then((r) => r.data),
    onMutate: (id) => setRefreshingId(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["playlists"] }),
    onError: () => toast.error("Refresh failed"),
    onSettled: () => setRefreshingId(null),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/playlists/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playlists"] });
      toast.success("Playlist removed");
    },
    onError: () => toast.error("Delete failed"),
  });

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return playlists;
    return playlists.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q)
    );
  }, [playlists, search]);

  return (
    <div className="p-lg space-y-md max-w-[1400px]">
      {/* Header */}
      <div className="flex justify-between items-end flex-wrap gap-md">
        <div>
          <h2 className="text-lg font-bold tracking-tight mb-0.5">Playlists</h2>
          <p className="text-on-surface-variant text-[12px]">
            {isLoading
              ? "Loading playlists…"
              : `${playlists.length} saved playlist${playlists.length === 1 ? "" : "s"}`}
          </p>
        </div>
        <button className="btn-primary" onClick={() => setShowAdd(true)}>
          <Plus size={16} /> Add Playlist
        </button>
      </div>

      {/* Search */}
      <div className="relative max-w-[28rem]">
        <MIcon name="search" size={18}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
        <input
          className="input pl-10"
          placeholder="Search playlists by name or description…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="py-16 flex justify-center text-on-surface-variant">
          <Loader2 size={24} className="animate-spin" />
        </div>
      ) : playlists.length === 0 ? (
        <EmptyState onAdd={() => setShowAdd(true)} />
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-on-surface-variant">No playlists match your search.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-gutter">
          {filtered.map((p) => (
            <PlaylistCard
              key={p.id}
              playlist={p}
              refreshing={refreshingId === p.id && refreshMut.isPending}
              onView={() => setViewing(p)}
              onRefresh={() => refreshMut.mutate(p.id)}
              onDelete={() => {
                if (confirm(`Remove "${p.name}" from your playlists?`)) deleteMut.mutate(p.id);
              }}
            />
          ))}
        </div>
      )}

      {showAdd && (
        <AddPlaylistModal
          onClose={() => setShowAdd(false)}
          onSaved={() => {
            setShowAdd(false);
            qc.invalidateQueries({ queryKey: ["playlists"] });
          }}
        />
      )}

      {viewing && <ChannelsDrawer playlist={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

/* ── Card ─────────────────────────────────────────────────────── */

function PlaylistCard({
  playlist, refreshing, onView, onRefresh, onDelete,
}: {
  playlist: Playlist;
  refreshing: boolean;
  onView: () => void;
  onRefresh: () => void;
  onDelete: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copyUrl() {
    const ok = await copyToClipboard(playlist.url);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      toast.error("Could not copy");
    }
  }

  return (
    <div className="bg-surface-container-low border border-outline-variant rounded-md p-md flex flex-col gap-3">
      {/* Title row */}
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-bold text-base leading-tight truncate" title={playlist.name}>
          {playlist.name}
        </h3>
        <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-code-label uppercase tracking-wider text-on-surface-variant border border-outline-variant rounded-full px-2 py-0.5">
          <Globe size={11} /> M3U
        </span>
      </div>

      {/* Channel count + health + avatar stack */}
      <div>
        <p className="text-on-surface-variant text-[12px] mb-2 flex items-center gap-1.5 flex-wrap">
          <span>{playlist.channel_count} channel{playlist.channel_count === 1 ? "" : "s"}</span>
          {playlist.health && (() => {
            const tone = healthTone(playlist.health, !!playlist.last_error);
            return (
              <>
                <span className="text-on-surface-variant/40">·</span>
                <span className={clsx(
                  "inline-flex items-center gap-1 font-medium",
                  tone === "good" && "text-[#5edc8a]",
                  tone === "warn" && "text-[#ffb4ab]",
                  tone === "muted" && "text-on-surface-variant"
                )}>
                  <span className={clsx(
                    "w-1.5 h-1.5 rounded-full",
                    tone === "good" && "bg-[#5edc8a]",
                    tone === "warn" && "bg-[#ffb4ab]",
                    tone === "muted" && "bg-on-surface-variant"
                  )} />
                  {playlist.health}
                </span>
              </>
            );
          })()}
        </p>
        <AvatarStack logos={playlist.logos} total={playlist.channel_count} />
      </div>

      {/* Description */}
      <p className={clsx("text-[12px] line-clamp-2", playlist.description ? "text-on-surface-variant" : "text-on-surface-variant/60 italic")}>
        {playlist.description || "No description provided"}
      </p>

      {/* URL + copy */}
      <div className="flex items-center gap-1.5 bg-surface-container rounded px-2 py-1.5 border border-outline-variant">
        <span className="text-[11px] text-on-surface-variant truncate font-mono flex-1" title={playlist.url}>
          {playlist.url}
        </span>
        <button
          onClick={copyUrl}
          title="Copy URL"
          className="shrink-0 text-on-surface-variant hover:text-on-surface transition-colors"
        >
          {copied ? <Check size={14} className="text-[#5edc8a]" /> : <Copy size={14} />}
        </button>
      </div>

      {/* Issues row */}
      {playlist.last_error && (
        <p className="flex items-center gap-1.5 text-[11px] badge-red rounded px-2 py-1">
          <AlertCircle size={13} className="shrink-0" /> {playlist.last_error}
        </p>
      )}

      {/* Last-checked timestamp */}
      <p className="text-[10px] text-on-surface-variant/60 mt-auto">
        Checked {timeAgo(playlist.last_refreshed)}
      </p>

      {/* Actions */}
      <div className="flex items-center gap-1.5 pt-0.5">
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="flex-1 inline-flex items-center justify-center gap-1.5 text-[12px] font-medium border border-outline-variant rounded px-2 py-1.5 text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors disabled:opacity-50"
        >
          <RefreshCw size={13} className={clsx(refreshing && "animate-spin")} /> Refresh
        </button>
        <button
          onClick={onView}
          className="flex-1 inline-flex items-center justify-center gap-1.5 text-[12px] font-medium border border-outline-variant rounded px-2 py-1.5 text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors"
        >
          <Eye size={13} /> View
        </button>
        <button
          onClick={onDelete}
          title="Delete"
          className="shrink-0 inline-flex items-center justify-center border border-outline-variant rounded px-2 py-1.5 text-on-surface-variant hover:text-error hover:border-error/40 transition-colors"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}

function AvatarStack({ logos, total }: { logos: string[]; total: number }) {
  const shown = logos.slice(0, 5);
  const extra = total - shown.length;
  if (shown.length === 0) {
    return <div className="h-8" />; // keep card heights aligned when no logos cached
  }
  return (
    <div className="flex items-center">
      <div className="flex -space-x-2">
        {shown.map((logo, i) => (
          <LogoAvatar key={i} logo={logo} />
        ))}
      </div>
      {extra > 0 && (
        <span className="ml-2 text-[11px] font-medium text-on-surface-variant">+{extra}</span>
      )}
    </div>
  );
}

function LogoAvatar({ logo }: { logo: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="w-8 h-8 rounded-full bg-surface-container-high border-2 border-surface-container-low flex items-center justify-center">
        <Globe size={12} className="text-on-surface-variant" />
      </div>
    );
  }
  return (
    <img
      src={logo}
      alt=""
      onError={() => setFailed(true)}
      className="w-8 h-8 rounded-full object-contain bg-surface-container-high border-2 border-surface-container-low"
    />
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="py-20 flex flex-col items-center text-center gap-3">
      <div className="w-12 h-12 rounded-full bg-surface-container flex items-center justify-center">
        <Globe size={22} className="text-on-surface-variant" />
      </div>
      <div>
        <p className="font-bold">No playlists yet</p>
        <p className="text-on-surface-variant text-[13px] max-w-sm">
          Add an M3U playlist URL to browse its channels and import them into your streams.
        </p>
      </div>
      <button className="btn-primary" onClick={onAdd}>
        <Plus size={16} /> Add Playlist
      </button>
    </div>
  );
}

/* ── Add modal ────────────────────────────────────────────────── */

function AddPlaylistModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    const t = toast.loading("Fetching playlist…");
    try {
      await api.post("/playlists", {
        name: name.trim(),
        url: url.trim(),
        description: description.trim() || null,
      });
      toast.success("Playlist added", { id: t });
      onSaved();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Could not add playlist", { id: t });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-surface-container border border-outline-variant rounded-md w-full max-w-[28rem] shadow-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold">Add Playlist</h2>
          <button onClick={onClose} className="text-on-surface-variant hover:text-on-surface">
            <X size={18} />
          </button>
        </div>
        <div>
          <label className="block text-xs font-medium text-on-surface-variant mb-1.5">Name *</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. UK Channels" />
        </div>
        <div>
          <label className="block text-xs font-medium text-on-surface-variant mb-1.5">M3U URL *</label>
          <input className="input font-mono text-[12px]" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/playlist.m3u" />
        </div>
        <div>
          <label className="block text-xs font-medium text-on-surface-variant mb-1.5">Description (optional)</label>
          <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="A short note about this playlist" />
        </div>
        <div className="flex gap-3 pt-1">
          <button className="btn-secondary flex-1 justify-center" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary flex-1 justify-center"
            onClick={save}
            disabled={saving || !name.trim() || !url.trim()}
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} Add
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Channels drawer (browse + import) ────────────────────────── */

interface Stream { id: number; name: string; stream_url: string; }
interface Category { id: number; name: string; }

function ChannelsDrawer({ playlist, onClose }: { playlist: Playlist; onClose: () => void }) {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  const { data: channels = [], isLoading, isError } = useQuery<PlaylistChannel[]>({
    queryKey: ["playlist-channels", playlist.id],
    queryFn: () =>
      api.get(`/playlists/${playlist.id}/channels`).then((r) => r.data.channels as PlaylistChannel[]),
    staleTime: 5 * 60_000,
  });

  const { data: streams = [] } = useQuery<Stream[]>({
    queryKey: ["streams"],
    queryFn: () => api.get("/streams").then((r) => r.data),
  });

  const importedUrls = useMemo(() => {
    const urls = new Set<string>();
    for (const s of streams) if (s.stream_url) urls.add(s.stream_url);
    return urls;
  }, [streams]);

  const isImported = (c: PlaylistChannel) => importedUrls.has(c.url);

  const categories = useMemo(
    () => Array.from(new Set(channels.map((c) => c.category))).sort(),
    [channels]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return channels.filter(
      (c) =>
        (!q || c.name.toLowerCase().includes(q)) &&
        (!categoryFilter || c.category === categoryFilter)
    );
  }, [channels, search, categoryFilter]);

  const selectableVisible = filtered.filter((c) => !isImported(c));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function importSelected() {
    const toImport = channels.filter((c) => selected.has(c.id) && !isImported(c));
    if (!toImport.length) return;
    setImporting(true);
    const t = toast.loading(`Importing ${toImport.length} channel${toImport.length === 1 ? "" : "s"}…`);
    try {
      // Import into a category named after the playlist (created if missing).
      const cats: Category[] = await api.get("/categories").then((r) => r.data);
      let cat = cats.find((c) => c.name === playlist.name);
      if (!cat) {
        cat = await api.post("/categories", { name: playlist.name }).then((r) => r.data);
      }

      let ok = 0;
      for (const c of toImport) {
        try {
          await api.post("/streams", {
            name: c.name,
            stream_url: c.url,
            sources: [c.url],
            delivery_mode: "restream",
            logo_url: c.logo || null,
            category_id: cat!.id,
            epg_channel_id: c.id && !c.id.startsWith("http") ? c.id : null,
          });
          ok++;
        } catch {
          /* keep importing the rest on individual failures */
        }
      }

      toast.success(`Imported ${ok} channel${ok === 1 ? "" : "s"} to ${playlist.name}`, { id: t });
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["streams"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Import failed", { id: t });
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex justify-end">
      <div className="absolute inset-0" onClick={onClose} aria-hidden />
      <div className="relative bg-surface w-full max-w-3xl h-full flex flex-col shadow-2xl border-l border-outline-variant">
        {/* Header */}
        <div className="shrink-0 border-b border-outline-variant px-lg py-md flex items-center justify-between gap-md">
          <div className="min-w-0">
            <h2 className="text-lg font-bold truncate">{playlist.name}</h2>
            <p className="text-on-surface-variant text-[12px]">
              {isLoading ? "Loading channels…" : `${channels.length} channels`}
            </p>
          </div>
          <div className="flex items-center gap-sm shrink-0">
            <button
              className="btn-primary"
              onClick={importSelected}
              disabled={importing || selected.size === 0}
            >
              {importing ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
              Import ({selected.size})
            </button>
            <button onClick={onClose} className="p-1.5 text-on-surface-variant hover:text-on-surface hover:bg-surface-container rounded-md transition-colors">
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="shrink-0 px-lg py-md flex gap-md flex-wrap items-center border-b border-outline-variant">
          <div className="relative flex-1 min-w-[180px]">
            <MIcon name="search" size={18}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
            <input
              className="input pl-10"
              placeholder="Search channels…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select className="input w-48" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
            <option value="">All categories</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button
            className="btn-secondary"
            onClick={() => setSelected((prev) => {
              const next = new Set(prev);
              selectableVisible.forEach((c) => next.add(c.id));
              return next;
            })}
            disabled={selectableVisible.length === 0}
          >
            Select All
          </button>
          <button className="btn-secondary" onClick={() => setSelected(new Set())} disabled={selected.size === 0}>
            Clear
          </button>
        </div>

        {/* Channel list */}
        <div className="flex-1 overflow-y-auto px-lg py-md">
          {isLoading && (
            <div className="py-16 flex justify-center text-on-surface-variant">
              <Loader2 size={24} className="animate-spin" />
            </div>
          )}
          {isError && (
            <div className="py-16 text-center text-on-surface-variant">
              Could not load channels. The playlist may be down — try Refresh.
            </div>
          )}
          {!isLoading && !isError && filtered.length === 0 && (
            <div className="py-16 text-center text-on-surface-variant">No channels match your filters.</div>
          )}
          {!isLoading && !isError && filtered.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-gutter">
              {filtered.map((c) => {
                const added = isImported(c);
                const checked = selected.has(c.id);
                return (
                  <div
                    key={c.id}
                    onClick={() => !added && toggle(c.id)}
                    className={clsx(
                      "bg-surface-container-low border p-md flex items-start gap-3 rounded-md transition-all",
                      added
                        ? "opacity-50 border-outline-variant"
                        : clsx("cursor-pointer hover:border-primary",
                            checked ? "border-primary-fixed-dim" : "border-outline-variant")
                    )}
                  >
                    <ChannelLogo logo={c.logo} name={c.name} />
                    <div className="flex-1 min-w-0">
                      <p className="font-bold text-body-sm truncate" title={c.name}>{c.name}</p>
                      <span className="inline-block mt-1.5 border border-outline-variant px-2 py-0.5 text-[10px] font-code-label uppercase tracking-wider text-on-surface-variant">
                        {c.category}
                      </span>
                    </div>
                    <div className="shrink-0">
                      {added ? (
                        <span className="badge-green text-[10px] whitespace-nowrap">Added</span>
                      ) : (
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggle(c.id)}
                          onClick={(e) => e.stopPropagation()}
                          className="w-4 h-4 accent-[var(--color-primary-fixed-dim)] cursor-pointer"
                        />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ChannelLogo({ logo, name }: { logo: string; name: string }) {
  const [failed, setFailed] = useState(false);
  const fallbackLetter = (name.trim()[0] || "?").toUpperCase();
  if (!logo || failed) {
    return (
      <div
        className="w-12 h-12 shrink-0 border border-outline-variant rounded flex items-center justify-center text-on-surface font-bold text-body-sm"
        style={{ backgroundColor: "#2a2a2a" }}
      >
        {fallbackLetter}
      </div>
    );
  }
  return (
    <img
      src={logo}
      alt=""
      onError={() => setFailed(true)}
      className="w-12 h-12 shrink-0 object-contain border border-outline-variant rounded p-1"
      style={{ backgroundColor: "#2a2a2a" }}
    />
  );
}
