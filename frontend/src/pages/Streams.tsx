import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Upload, Search, Play, Trash2, Edit2,
  RefreshCw, CheckCircle, XCircle, Loader2, TestTube,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";

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
  const [search, setSearch] = useState("");
  const [filterCat, setFilterCat] = useState<number | "">("");
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
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Streams</h1>
        <div className="flex gap-2">
          <input ref={fileRef} type="file" accept=".m3u,.m3u8" className="hidden" onChange={handleM3UUpload} />
          <button className="btn-secondary" onClick={() => fileRef.current?.click()}>
            <Upload size={15} /> Import M3U
          </button>
          <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
            <Plus size={15} /> Add Stream
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="input pl-9"
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
      <div className="card overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-200 bg-gray-50 text-xs">
                <th className="px-4 py-3 font-medium">Stream</th>
                <th className="px-4 py-3 font-medium">Category</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Viewers</th>
                <th className="px-4 py-3 font-medium">Enabled</th>
                <th className="px-4 py-3 font-medium w-32">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                    <Loader2 size={20} className="animate-spin mx-auto" />
                  </td>
                </tr>
              )}
              {!isLoading && streams.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                    No streams found. Add one or import an M3U file.
                  </td>
                </tr>
              )}
              {streams.map((s) => {
                const cat = categories.find((c) => c.id === s.category_id);
                return (
                  <tr key={s.id} className="border-b border-gray-100 table-row-hover">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        {s.logo_url ? (
                          <img
                            src={s.logo_url}
                            alt=""
                            className="w-8 h-8 object-contain bg-gray-100 border border-gray-200"
                            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                          />
                        ) : (
                          <div className="w-8 h-8 bg-gray-100 border border-gray-200 flex items-center justify-center">
                            <Play size={12} className="text-gray-400" />
                          </div>
                        )}
                        <div>
                          <div className="flex items-center gap-1.5">
                            <p className="text-gray-900 font-medium">{s.name}</p>
                            {s.delivery_mode === "balanced" && (
                              <span className="badge-blue text-[10px]" title="Load-balanced across source mirrors">balanced</span>
                            )}
                            {(s.source_count ?? 0) > 1 && (
                              <span className="text-[10px] text-gray-400" title="Failover sources">
                                {s.source_count} sources
                              </span>
                            )}
                          </div>
                          <p className="text-gray-400 text-xs truncate max-w-xs font-mono">{s.stream_url}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {cat?.name ?? <span className="text-gray-300">Uncategorized</span>}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={s.status} />
                    </td>
                    <td className="px-4 py-3 text-gray-600">{s.viewer_count}</td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => toggleMutation.mutate(s.id)}
                        className={s.is_enabled ? "text-green-600 hover:text-green-700" : "text-gray-300 hover:text-gray-500"}
                      >
                        {s.is_enabled ? <CheckCircle size={16} /> : <XCircle size={16} />}
                      </button>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <button title="Test URL"
                          className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                          onClick={() => testMutation.mutate(s.id)}>
                          <TestTube size={14} />
                        </button>
                        <button title="Restart"
                          className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                          onClick={() => restartMutation.mutate(s.id)}>
                          <RefreshCw size={14} />
                        </button>
                        <button title="Edit"
                          className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                          onClick={() => { setEditing(s); setShowModal(true); }}>
                          <Edit2 size={14} />
                        </button>
                        <button title="Delete"
                          className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-gray-100 transition-colors"
                          onClick={() => { if (confirm(`Delete "${s.name}"?`)) deleteMutation.mutate(s.id); }}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    running: "badge-green",
    starting: "badge-yellow",
    error: "badge-red",
    stopped: "badge-gray",
    idle: "badge-gray",
  };
  return <span className={map[status] || "badge-gray"}>{status}</span>;
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
      <div className="bg-white border border-gray-300 w-full max-w-md p-6 space-y-4 max-h-[90vh] overflow-y-auto">
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
