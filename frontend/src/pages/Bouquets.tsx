import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Edit2, Package, Check } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";

interface Category { id: number; name: string; }
interface Bouquet {
  id: number; name: string; description?: string;
  categories: Array<{ id: number; name: string; sort_order: number }>;
}

export default function Bouquets() {
  const qc = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Bouquet | null>(null);

  const { data: bouquets = [] } = useQuery<Bouquet[]>({
    queryKey: ["bouquets"],
    queryFn: () => api.get("/bouquets").then((r) => r.data),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/bouquets/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["bouquets"] }); toast.success("Bouquet deleted"); },
  });

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Bouquets</h1>
        <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
          <Plus size={15} /> New Bouquet
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {bouquets.map((b) => (
          <div key={b.id} className="card group">
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 bg-gray-100 border border-gray-200 rounded-lg flex items-center justify-center">
                  <Package size={15} className="text-gray-500" />
                </div>
                <div>
                  <p className="text-gray-900 font-medium">{b.name}</p>
                  {b.description && <p className="text-gray-400 text-xs">{b.description}</p>}
                </div>
              </div>
              <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
                  onClick={() => { setEditing(b); setShowModal(true); }}
                >
                  <Edit2 size={14} />
                </button>
                <button
                  className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                  onClick={() => { if (confirm(`Delete "${b.name}"?`)) deleteMut.mutate(b.id); }}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {b.categories.map((c) => (
                <span key={c.id} className="badge-gray">{c.name}</span>
              ))}
              {b.categories.length === 0 && (
                <span className="text-gray-400 text-xs">No categories assigned</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {showModal && (
        <BouquetModal
          bouquet={editing}
          onClose={() => setShowModal(false)}
          onSaved={() => { setShowModal(false); qc.invalidateQueries({ queryKey: ["bouquets"] }); }}
        />
      )}
    </div>
  );
}

function BouquetModal({
  bouquet, onClose, onSaved,
}: {
  bouquet: Bouquet | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(bouquet?.name ?? "");
  const [desc, setDesc] = useState(bouquet?.description ?? "");
  const [selectedCats, setSelectedCats] = useState<number[]>(
    bouquet?.categories.map((c) => c.id) ?? []
  );
  const [saving, setSaving] = useState(false);

  const { data: allCats = [] } = useQuery<Category[]>({
    queryKey: ["categories"],
    queryFn: () => api.get("/categories").then((r) => r.data),
  });

  function toggleCat(id: number) {
    setSelectedCats((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  }

  async function save() {
    setSaving(true);
    try {
      let id = bouquet?.id;
      if (bouquet) {
        await api.put(`/bouquets/${bouquet.id}`, { name, description: desc || null });
      } else {
        const r = await api.post("/bouquets", { name, description: desc || null });
        id = r.data.id;
      }
      await api.post(`/bouquets/${id}/categories`, { category_ids: selectedCats });
      toast.success(bouquet ? "Bouquet updated" : "Bouquet created");
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

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Name *</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Description</label>
          <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-2">Assign Categories</label>
          <div className="max-h-48 overflow-y-auto space-y-1 border border-gray-200 rounded-lg p-2 bg-gray-50">
            {allCats.map((c) => (
              <button
                key={c.id}
                onClick={() => toggleCat(c.id)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors
                  ${selectedCats.includes(c.id)
                    ? "bg-gray-900 text-white font-medium"
                    : "bg-white text-gray-700 hover:bg-gray-100 border border-gray-200"}`}
              >
                {c.name}
                {selectedCats.includes(c.id) && <Check size={14} />}
              </button>
            ))}
          </div>
        </div>
        <div className="flex gap-3 pt-1">
          <button className="btn-secondary flex-1 justify-center" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex-1 justify-center" onClick={save} disabled={saving || !name}>
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
