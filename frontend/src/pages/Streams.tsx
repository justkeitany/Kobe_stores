import { useState, useRef, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Loader2, TestTube } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { MIcon } from "../components/MIcon";
import clsx from "clsx";

interface Stream {
  id: number;
  name: string;
  stream_url: string;
  logo_url?: string;
  category_id?: number;
  is_enabled: boolean;
  status: string;
  viewer_count: number;
  last_error?: string;
  delivery_mode?: "restream" | "balanced";
  source_count?: number;
}

interface Category {
  id: number;
  name: string;
}

export default function Streams() {
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [filterCat, setFilterCat] = useState<number | "">("");

  // Sync with the global header search (?q=…) on navigation.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Stream | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: streams = [], isLoading } = useQuery<Stream[]>({
    queryKey: ["streams", search, filterCat],
    queryFn: () =>
      api.get("/streams", {
        params: { search: search || undefined, category_id: filterCat || undefined },
      }).then((r) => r.data),
    refetchInterval: 5000,
  });

  const { data: categories = [] } = useQuery<Category[]>({
    queryKey: ["categories"],
    queryFn: () => api.get("/categories").then((r) => r.data),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/streams/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["streams"] }); toast.success("Stream deleted"); },
  });

  const toggleMutation = useMutation({
    mutationFn: (id: number) => api.post(`/streams/${id}/toggle`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["streams"] }),
  });

  const restartMutation = useMutation({
    mutationFn: (id: number) => api.post(`/streams/${id}/restart`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["streams"] }); toast.success("Stream restarted"); },
  });

  const testMutation = useMutation({
    mutationFn: (id: number) => api.post(`/streams/${id}/test`).then((r) => r.data),
    onSuccess: (data) => {
      if (data.alive) toast.success(`Stream OK: ${data.message}`);
      else toast.error(`Stream dead: ${data.message}`);
    },
  });

  async function handleM3UUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const t = toast.loading("Importing M3U...");
    try {
      const r = await api.post("/streams/import/m3u", form);
      toast.success(`Imported ${r.data.imported} channels (${r.data.skipped} skipped)`, { id: t });
      qc.invalidateQueries({ queryKey: ["streams"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Import failed", { id: t });
    }
    e.target.value = "";
  }

  return (
    <div className="p-lg space-y-md">
      {/* Header */}
      <div className="flex justify-between items-end flex-wrap gap-md">
        <div>
          <h2 className="text-lg font-bold tracking-tight mb-0.5">Streams</h2>
          <p className="text-on-surface-variant text-[12px]">
            Managing {streams.length.toLocaleString()} broadcast endpoint{streams.length === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex gap-md">
          <input ref={fileRef} type="file" accept=".m3u,.m3u8" className="hidden" onChange={handleM3UUpload} />
          <button className="btn-secondary" onClick={() => fileRef.current?.click()}>
            <MIcon name="file_upload" size={18} /> Import M3U
          </button>
          <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
            <MIcon name="add" size={18} /> Add Stream
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-md flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <MIcon name="search" size={18}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
          <input
            className="input pl-10"
            placeholder="Search streams..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="input w-44"
          value={filterCat}
          onChange={(e) => setFilterCat(e.target.value ? Number(e.target.value) : "")}
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="bg-surface-container-low border border-outline-variant overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-body-sm border-collapse">
            <thead>
              <tr className="border-b border-outline-variant bg-surface-container-high/30">
                <th className="px-md py-2.5 font-code-label uppercase text-[10px] tracking-wider text-on-surface-variant">Stream</th>
                <th className="px-md py-2.5 font-code-label uppercase text-[10px] tracking-wider text-on-surface-variant">Category</th>
                <th className="px-md py-2.5 font-code-label uppercase text-[10px] tracking-wider text-on-surface-variant">Status</th>
                <th className="px-md py-2.5 font-code-label uppercase text-[10px] tracking-wider text-on-surface-variant text-right">Viewers</th>
                <th className="px-md py-2.5 font-code-label uppercase text-[10px] tracking-wider text-on-surface-variant text-center">Enabled</th>
                <th className="px-md py-2.5 font-code-label uppercase text-[10px] tracking-wider text-on-surface-variant text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/50">
              {isLoading && (
                <tr>
                  <td colSpan={6} className="px-md py-6 text-center text-on-surface-variant">
                    <Loader2 size={18} className="animate-spin mx-auto" />
                  </td>
                </tr>
              )}
              {!isLoading && streams.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-md py-6 text-center text-on-surface-variant">
                    No streams found. Add one or import an M3U file.
                  </td>
                </tr>
              )}
              {streams.map((s) => {
                const cat = categories.find((c) => c.id === s.category_id);
                const offline = !s.is_enabled || s.status === "error";
                return (
                  <tr key={s.id} className={clsx("table-row-hover group", offline && "opacity-60")}>
                    <td className="px-md py-2">
                      <div className="flex items-center gap-2.5">
                        {s.logo_url ? (
                          <img
                            src={s.logo_url}
                            alt=""
                            className="w-8 h-8 object-cover bg-surface-container border border-outline-variant"
                            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                          />
                        ) : (
                          <div className="w-8 h-8 bg-surface-container border border-outline-variant flex items-center justify-center">
                            <MIcon name="live_tv" size={16} className="text-on-surface-variant opacity-50" />
                          </div>
                        )}
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <p className="font-bold truncate">{s.name}</p>
                            {s.delivery_mode === "balanced" && (
                              <span className="badge-blue text-[10px]" title="Load-balanced across source mirrors">balanced</span>
                            )}
                            {(s.source_count ?? 0) > 1 && (
                              <span className="text-[10px] text-on-surface-variant" title="Failover sources">
                                {s.source_count} sources
                              </span>
                            )}
                          </div>
                          <p className="text-[11px] font-code-label text-on-surface-variant/70 truncate max-w-xs">{s.stream_url}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-md py-2">
                      {cat?.name
                        ? <span className="border border-outline-variant px-2 py-0.5 text-[11px] font-code-label">{cat.name}</span>
                        : <span className="text-on-surface-variant/50 text-[11px]">Uncategorized</span>}
                    </td>
                    <td className="px-md py-2"><StatusCell status={s.status} enabled={s.is_enabled} /></td>
                    <td className={clsx("px-md py-2 text-right font-code-label", s.viewer_count > 0 && "font-bold text-primary-fixed-dim")}>
                      {s.viewer_count}
                    </td>
                    <td className="px-md py-2 text-center">
                      <button onClick={() => toggleMutation.mutate(s.id)} title={s.is_enabled ? "Disable" : "Enable"}>
                        {s.is_enabled
                          ? <MIcon name="check_circle" fill size={18} className="text-green-500" />
                          : <MIcon name="cancel" size={18} className="text-outline" />}
                      </button>
                    </td>
                    <td className="px-md py-2 text-right">
                      <div className="flex justify-end gap-1.5 opacity-40 group-hover:opacity-100 transition-opacity">
                        <button title="Test URL" className="p-1 hover:text-primary-fixed-dim transition-colors"
                          onClick={() => testMutation.mutate(s.id)}>
                          <MIcon name="science" size={18} />
                        </button>
                        <button title="Restart" className="p-1 hover:text-primary-fixed-dim transition-colors"
                          onClick={() => restartMutation.mutate(s.id)}>
                          <MIcon name="refresh" size={18} />
                        </button>
                        <button title="Edit" className="p-1 hover:text-primary-fixed-dim transition-colors"
                          onClick={() => { setEditing(s); setShowModal(true); }}>
                          <MIcon name="edit" size={18} />
                        </button>
                        <button title="Delete" className="p-1 hover:text-red-400 transition-colors"
                          onClick={() => { if (confirm(`Delete "${s.name}"?`)) deleteMutation.mutate(s.id); }}>
                          <MIcon name="delete" size={18} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!isLoading && streams.length > 0 && (
          <div className="px-md py-2.5 bg-surface-container flex items-center justify-between border-t border-outline-variant">
            <p className="text-on-surface-variant text-[12px]">
              Showing {streams.length.toLocaleString()} stream{streams.length === 1 ? "" : "s"}
            </p>
          </div>
        )}
      </div>

      {showModal && (
        <StreamModal
          stream={editing}
          categories={categories}
          onClose={() => setShowModal(false)}
          onSaved={() => { setShowModal(false); qc.invalidateQueries({ queryKey: ["streams"] }); }}
        />
      )}
    </div>
  );
}

function StatusCell({ status, enabled }: { status: string; enabled: boolean }) {
  const display = !enabled ? "offline" : status;
  const cfg: Record<string, { dot: string; text: string }> = {
    running:  { dot: "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]", text: "text-green-500" },
    starting: { dot: "bg-yellow-500", text: "text-yellow-500" },
    error:    { dot: "bg-red-500", text: "text-red-500" },
    offline:  { dot: "bg-red-500", text: "text-red-500" },
    idle:     { dot: "bg-outline opacity-40", text: "opacity-60" },
    stopped:  { dot: "bg-outline opacity-40", text: "opacity-60" },
  };
  const c = cfg[display] ?? cfg.idle;
  return (
    <div className="flex items-center gap-2">
      <span className={clsx("w-2 h-2 rounded-full", c.dot)} />
      <span className={clsx("text-[12px] uppercase font-bold tracking-widest", c.text)}>{display}</span>
    </div>
  );
}

function StreamModal({
  stream, categories, onClose, onSaved,
}: {
  stream: Stream | null;
  categories: Category[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(stream?.name ?? "");
  const [sources, setSources] = useState<string[]>([stream?.stream_url ?? ""]);
  const [logo, setLogo] = useState(stream?.logo_url ?? "");
  const [catId, setCatId] = useState<number | "">(stream?.category_id ?? "");
  const [deliveryMode, setDeliveryMode] = useState<"restream" | "balanced">(
    stream?.delivery_mode ?? "restream"
  );
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);

  // Load the full source pool when editing an existing stream.
  useEffect(() => {
    if (!stream) return;
    api.get(`/streams/${stream.id}`).then((r) => {
      const urls = (r.data.sources ?? []).map((s: any) => s.url);
      setSources(urls.length ? urls : [r.data.stream_url ?? ""]);
      setDeliveryMode(r.data.delivery_mode ?? "restream");
    }).catch(() => {});
  }, [stream]);

  function setSourceAt(i: number, val: string) {
    setSources((prev) => prev.map((u, idx) => (idx === i ? val : u)));
  }
  function addSource() { setSources((prev) => [...prev, ""]); }
  function removeSource(i: number) {
    setSources((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev));
  }
  function moveSource(i: number, dir: -1 | 1) {
    setSources((prev) => {
      const j = i + dir;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  async function testSource(i: number) {
    const url = sources[i]?.trim();
    if (!url) return;
    setTesting(i);
    try {
      const r = await api.post("/streams/sources/test", { url });
      if (r.data.alive) toast.success(`Source OK: ${r.data.message}`);
      else toast.error(`Source dead: ${r.data.message}`);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Test failed");
    } finally {
      setTesting(null);
    }
  }

  async function save() {
    const cleaned = sources.map((u) => u.trim()).filter(Boolean);
    if (!cleaned.length) { toast.error("Add at least one source URL"); return; }
    setSaving(true);
    try {
      const payload = {
        name,
        stream_url: cleaned[0],
        sources: cleaned,
        delivery_mode: deliveryMode,
        logo_url: logo || null,
        category_id: catId || null,
      };
      if (stream) {
        await api.put(`/streams/${stream.id}`, payload);
      } else {
        await api.post("/streams", payload);
      }
      toast.success(stream ? "Stream updated" : "Stream created");
      onSaved();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-300 rounded-md w-full max-w-md p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold text-gray-900">
          {stream ? "Edit Stream" : "Add Stream"}
        </h2>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Channel Name *</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. BBC News" />
        </div>

        {/* Sources / failover pool */}
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">
            Source URLs * <span className="text-gray-400 font-normal">(first = primary)</span>
          </label>
          <div className="space-y-2">
            {sources.map((u, i) => (
              <div key={i} className="flex gap-1.5 items-center">
                <span className="text-xs text-gray-400 w-4 text-right">{i + 1}</span>
                <input
                  className="input flex-1"
                  value={u}
                  onChange={(e) => setSourceAt(i, e.target.value)}
                  placeholder={i === 0 ? "http://primary..." : "http://backup mirror..."}
                />
                <button type="button" title="Test" disabled={testing === i}
                  className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100"
                  onClick={() => testSource(i)}>
                  {testing === i ? <Loader2 size={14} className="animate-spin" /> : <TestTube size={14} />}
                </button>
                <button type="button" title="Move up" disabled={i === 0}
                  className="p-1 text-gray-300 hover:text-gray-700 disabled:opacity-30"
                  onClick={() => moveSource(i, -1)}>↑</button>
                <button type="button" title="Move down" disabled={i === sources.length - 1}
                  className="p-1 text-gray-300 hover:text-gray-700 disabled:opacity-30"
                  onClick={() => moveSource(i, 1)}>↓</button>
                <button type="button" title="Remove" disabled={sources.length <= 1}
                  className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-gray-100 disabled:opacity-30"
                  onClick={() => removeSource(i)}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
          <button type="button" onClick={addSource}
            className="mt-2 text-xs text-gray-600 hover:text-gray-900 inline-flex items-center gap-1">
            <Plus size={12} /> Add source
          </button>
        </div>

        {/* Delivery mode */}
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Delivery mode</label>
          <select className="input" value={deliveryMode}
            onChange={(e) => setDeliveryMode(e.target.value as "restream" | "balanced")}>
            <option value="restream">Restream (FFmpeg) — sources used as failover chain</option>
            <option value="balanced">Balanced — spread viewers across source mirrors</option>
          </select>
          <p className="text-xs text-gray-400 mt-1">
            {deliveryMode === "balanced"
              ? "Players are sent directly to a healthy mirror, picked per user. Best for offloading many viewers across equivalent origins."
              : "One FFmpeg process restreams the channel; on failure it fails over to the next source."}
          </p>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Logo URL</label>
          <input className="input" value={logo} onChange={(e) => setLogo(e.target.value)} placeholder="https://..." />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Category</label>
          <select className="input" value={catId} onChange={(e) => setCatId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">Uncategorized</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>

        <div className="flex gap-3 pt-1">
          <button className="btn-secondary flex-1 justify-center" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex-1 justify-center" onClick={save}
            disabled={saving || !name || !sources.some((u) => u.trim())}>
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
