import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { LogoCard } from "../components/LogoCard";
import { Pagination } from "../components/Pagination";

const PAGE_SIZE = 36;

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
  const [page, setPage] = useState(0);

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

  const pages = Math.max(1, Math.ceil(cats.length / PAGE_SIZE));
  const cur = Math.min(page, pages - 1);
  const shown = cats.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 tracking-tight">Categories</h1>
        <button className="btn-primary" onClick={() => { setEditing(null); setShowModal(true); }}>
          <Plus size={15} /> Add Category
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-400">Loading...</p>
      ) : cats.length === 0 ? (
        <p className="text-gray-400">No categories yet. Add one to group your streams.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-5">
            {shown.map((cat) => (
              <LogoCard
                key={cat.id}
                name={cat.name}
                logo={cat.icon}
                noPlay
                onClick={() => { setEditing(cat); setShowModal(true); }}
                actions={
                  <button
                    className="danger"
                    title="Delete"
                    onClick={() => {
                      if (confirm(`Delete "${cat.name}"? Streams will be moved to Uncategorized.`))
                        deleteMut.mutate(cat.id);
                    }}
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
    <div className="modal-backdrop fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="modal-panel bg-white border border-gray-200 rounded-[10px] w-full max-w-[24rem] shadow-xl p-6 space-y-4">
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
