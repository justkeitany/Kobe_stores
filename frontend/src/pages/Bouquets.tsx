import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Check } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { LogoCard } from "../components/LogoCard";
import { Pagination } from "../components/Pagination";

const PAGE_SIZE = 36;

interface Category { id: number; name: string; }
interface Bouquet {
  id: number; name: string; description?: string;
  categories: Array<{ id: number; name: string; sort_order: number }>;
}

export default function Bouquets() {
  const qc = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Bouquet | null>(null);
  const [page, setPage] = useState(0);

  const { data: bouquets = [], isLoading } = useQuery<Bouquet[]>({
    queryKey: ["bouquets"],
    queryFn: () => api.get("/bouquets").then((r) => r.data),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/bouquets/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["bouquets"] }); toast.success("Bouquet deleted"); },
  });

  const pages = Math.max(1, Math.ceil(bouquets.length / PAGE_SIZE));
  const cur = Math.min(page, pages - 1);
  const shown = bouquets.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Bouquets</h1>
        <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
          <Plus size={15} /> New Bouquet
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-400">Loading...</p>
      ) : bouquets.length === 0 ? (
        <p className="text-gray-400">No bouquets yet. Create one to group categories.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-5">
            {shown.map((b) => (
              <LogoCard
                key={b.id}
                name={b.name}
                noPlay
                onClick={() => { setEditing(b); setShowModal(true); }}
                actions={
                  <button
                    className="danger"
                    title="Delete"
                    onClick={() => { if (confirm(`Delete "${b.name}"?`)) deleteMut.mutate(b.id); }}
                  >
                    <Trash2 size={13} />
                  </button>
                }
              />
            ))}
          </div>
          <div className="pt-2">
            <Pagination page={cur + 1} totalPages={pages} onChange={(p) => setPage(p - 1)} />
          </div>
        </>
      )}

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
      <div className="bg-white border border-gray-200 rounded-[10px] w-full max-w-[28rem] shadow-xl p-6 space-y-4">

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
