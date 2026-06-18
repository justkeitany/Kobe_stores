import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, RefreshCw, Radio } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";

interface EpgSource {
  id: number;
  name: string;
  url: string;
  is_enabled: boolean;
  last_updated?: string;
  update_interval_hours: number;
}

export default function EPG() {
  const qc = useQueryClient();
  const [showModal, setShowModal] = useState(false);

  const { data: sources = [] } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources"],
    queryFn: () => api.get("/epg/sources").then((r) => r.data),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/epg/sources/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["epg-sources"] }); toast.success("EPG source removed"); },
  });

  const refreshMut = useMutation({
    mutationFn: (id: number) => api.post(`/epg/sources/${id}/refresh`),
    onSuccess: () => toast.success("EPG refresh started in background"),
  });

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">EPG / XMLTV</h1>
        <button className="btn-primary" onClick={() => setShowModal(true)}>
          <Plus size={15} /> Add EPG Source
        </button>
      </div>

      <div className="space-y-3">
        {sources.length === 0 && (
          <div className="card text-center text-gray-400 py-12">
            <Radio size={32} className="mx-auto mb-3 opacity-30" />
            <p>No EPG sources yet. Add an XMLTV URL to get guide data.</p>
          </div>
        )}
        {sources.map((s) => (
          <div key={s.id} className="card flex items-center gap-4 group">
            <div className="w-9 h-9 bg-gray-100 border border-gray-200 rounded-lg flex items-center justify-center shrink-0">
              <Radio size={15} className="text-gray-500" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-gray-900 font-medium">{s.name}</p>
              <p className="text-gray-400 text-xs truncate">{s.url}</p>
              {s.last_updated && (
                <p className="text-gray-400 text-xs">
                  Last updated: {new Date(s.last_updated).toLocaleString()}
                </p>
              )}
            </div>
            <span className="text-gray-400 text-xs whitespace-nowrap">
              Every {s.update_interval_hours}h
            </span>
            <div className="flex gap-1">
              <button
                className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
                onClick={() => refreshMut.mutate(s.id)}
                title="Refresh now"
              >
                <RefreshCw size={14} />
              </button>
              <button
                className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                onClick={() => { if (confirm("Remove EPG source?")) deleteMut.mutate(s.id); }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h2 className="text-sm font-semibold text-gray-900 mb-2">Your XMLTV endpoint</h2>
        <p className="text-xs text-gray-500 mb-3">
          Use this URL in your player to get EPG guide data:
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 bg-gray-50 border border-gray-200 text-gray-700 text-xs px-3 py-2 rounded-lg truncate font-mono">
            {window.location.origin}/xmltv.php?username=YOUR_USER&password=YOUR_PASS
          </code>
          <button
            className="btn-secondary text-xs"
            onClick={() => {
              navigator.clipboard.writeText(
                `${window.location.origin}/xmltv.php?username=admin&password=YOUR_PASS`
              );
              toast.success("Copied");
            }}
          >
            Copy
          </button>
        </div>
      </div>

      {showModal && (
        <EpgModal
          onClose={() => setShowModal(false)}
          onSaved={() => { setShowModal(false); qc.invalidateQueries({ queryKey: ["epg-sources"] }); }}
        />
      )}
    </div>
  );
}

function EpgModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [interval, setInterval] = useState(24);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await api.post("/epg/sources", { name, url, update_interval_hours: interval });
      toast.success("EPG source added, fetching data...");
      onSaved();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-200 rounded-[10px] w-full max-w-[28rem] shadow-xl p-6 space-y-4">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Name *</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. EPG.best" />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">XMLTV URL *</label>
          <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Update every (hours)</label>
          <input
            className="input" type="number" min={1} max={168}
            value={interval} onChange={(e) => setInterval(Number(e.target.value))}
          />
        </div>
        <div className="flex gap-3 pt-1">
          <button className="btn-secondary flex-1 justify-center" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex-1 justify-center" onClick={save} disabled={saving || !name || !url}>
            {saving ? "Adding..." : "Add Source"}
          </button>
        </div>
      </div>
    </div>
  );
}
