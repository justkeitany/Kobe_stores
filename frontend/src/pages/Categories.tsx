import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Edit2, FolderOpen } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";

interface Category {
  id: number;
  name: string;
  icon?: string;
  sort_order: number;
  stream_count: number;
}

export default function Categories() {
  const qc = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Category | null>(null);

  const { data: cats = [], isLoading } = useQuery<Category[]>({
    queryKey: ["categories"],
    queryFn: () => api.get("/categories").then((r) => r.data),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.delete(`/categories/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      toast.success("Category deleted (streams moved to Uncategorized)");
    },
  });

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Categories</h1>
        <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
          <Plus size={15} /> Add Category
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {isLoading && <p className="text-gray-400 col-span-3">Loading...</p>}
        {cats.map((cat) => (
          <div key={cat.id} className="card flex items-center gap-4 group">
            <div className="w-10 h-10 bg-gray-100 border border-gray-200 rounded-lg flex items-center justify-center shrink-0">
              {cat.icon ? (
                <img src={cat.icon} alt="" className="w-6 h-6 object-contain" />
              ) : (
                <FolderOpen size={16} className="text-gray-500" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-gray-900 font-medium truncate">{cat.name}</p>
              <p className="text-gray-400 text-xs">{cat.stream_count} streams</p>
            </div>
            <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                className="p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
                onClick={() => { setEditing(cat); setShowModal(true); }}
              >
                <Edit2 size={14} />
              </button>
              <button
                className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                onClick={() => {
                  if (confirm(`Delete "${cat.name}"? Streams will be moved to Uncategorized.`))
                    deleteMut.mutate(cat.id);
                }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {showModal && (
        <CategoryModal
          cat={editing}
          onClose={() => setShowModal(false)}
          onSaved={() => { setShowModal(false); qc.invalidateQueries({ queryKey: ["categories"] }); }}
        />
      )}
    </div>
  );
}

function CategoryModal({
  cat, onClose, onSaved,
}: {
  cat: Category | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(cat?.name ?? "");
  const [icon, setIcon] = useState(cat?.icon ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      if (cat) {
        await api.put(`/categories/${cat.id}`, { name, icon: icon || null });
      } else {
        await api.post("/categories", { name, icon: icon || null });
      }
      toast.success(cat ? "Category updated" : "Category created");
      onSaved();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-200 rounded-[10px] w-full max-w-sm shadow-xl p-6 space-y-4">
        <h2 className="text-lg font-semibold text-gray-900">
          {cat ? "Edit Category" : "New Category"}
        </h2>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Name *</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sports" />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1.5">Icon URL (optional)</label>
          <input className="input" value={icon} onChange={(e) => setIcon(e.target.value)} placeholder="https://..." />
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
