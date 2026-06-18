import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { MIcon } from "../components/MIcon";
import clsx from "clsx";

const PLUTO_CATEGORY = "Pluto TVs";

interface PlutoRaw {
  _id: string;
  name: string;
  category?: string;
  logo?: { path?: string };
  images?: { logo?: { path?: string } };
  stitched?: { urls?: { url?: string }[] };
}

interface PlutoChannel {
  id: string;
  name: string;
  category: string;
  logo: string;
  baseUrl: string;
}

interface Stream {
  id: number;
  name: string;
  stream_url: string;
}

interface Category {
  id: number;
  name: string;
}

function normalize(raw: PlutoRaw[]): PlutoChannel[] {
  const out: PlutoChannel[] = [];
  for (const c of raw) {
    const url = c.stitched?.urls?.[0]?.url;
    if (!url) continue;
    out.push({
      id: c._id,
      name: c.name,
      category: c.category || "Uncategorized",
      logo: c.logo?.path || c.images?.logo?.path || "",
      baseUrl: url.split("?")[0],
    });
  }
  return out;
}

export default function PlutoTV() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  const { data: channels = [], isLoading, isError } = useQuery<PlutoChannel[]>({
    queryKey: ["pluto-channels"],
    queryFn: () => api.get("/pluto/channels").then((r) => normalize(r.data)),
    staleTime: 10 * 60_000,
  });

  // Existing streams — used to flag already-imported channels.
  const { data: streams = [] } = useQuery<Stream[]>({
    queryKey: ["pluto-streams"],
    queryFn: () => api.get("/streams").then((r) => r.data),
  });

  const imported = useMemo(() => {
    const ids = new Set<string>();
    const urls = new Set<string>();
    for (const s of streams) {
      if (!s.stream_url) continue;
      urls.add(s.stream_url);
      const m = s.stream_url.match(/\/channel\/([a-f0-9]+)\//i);
      if (m) ids.add(m[1]);
    }
    return { ids, urls };
  }, [streams]);

  const isImported = (c: PlutoChannel) =>
    imported.ids.has(c.id) || imported.urls.has(c.baseUrl);

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
  const selectedCount = selected.size;

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function selectAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      selectableVisible.forEach((c) => next.add(c.id));
      return next;
    });
  }

  function deselectAll() {
    setSelected(new Set());
  }

  async function importSelected() {
    const toImport = channels.filter((c) => selected.has(c.id) && !isImported(c));
    if (!toImport.length) return;
    setImporting(true);
    const t = toast.loading(`Importing ${toImport.length} channel${toImport.length === 1 ? "" : "s"}…`);
    try {
      // 1. Ensure the "Pluto TVs" category exists.
      const cats: Category[] = await api.get("/categories").then((r) => r.data);
      let cat = cats.find((c) => c.name === PLUTO_CATEGORY);
      if (!cat) {
        cat = await api.post("/categories", { name: PLUTO_CATEGORY }).then((r) => r.data);
      }

      // 2. Create one stream per selected channel.
      let ok = 0;
      for (const c of toImport) {
        try {
          await api.post("/streams", {
            name: c.name,
            stream_url: c.baseUrl,
            sources: [c.baseUrl],
            delivery_mode: "restream",
            logo_url: c.logo || null,
            category_id: cat!.id,
          });
          ok++;
        } catch {
          /* skip individual channel failures, keep importing the rest */
        }
      }

      toast.success(`Imported ${ok} channels to ${PLUTO_CATEGORY} successfully`, { id: t });
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["pluto-streams"] });
      qc.invalidateQueries({ queryKey: ["streams"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Import failed", { id: t });
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="p-lg space-y-md max-w-[1400px]">
      {/* Header */}
      <div className="flex justify-between items-end flex-wrap gap-md">
        <div>
          <h2 className="text-lg font-bold tracking-tight mb-0.5">Pluto TV</h2>
          <p className="text-on-surface-variant text-[12px]">
            {isLoading ? "Loading channels…" : `${channels.length.toLocaleString()} channels`}
          </p>
        </div>
        <button
          className="btn-primary"
          onClick={importSelected}
          disabled={importing || selectedCount === 0}
        >
          {importing ? <Loader2 size={18} className="animate-spin" /> : <MIcon name="download" size={18} />}
          Import Selected ({selectedCount})
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-md flex-wrap items-center">
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <MIcon name="search" size={18}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
          <input
            className="input pl-10"
            placeholder="Search channels by name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="input w-56"
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <div className="flex gap-sm ml-auto">
          <button className="btn-secondary" onClick={selectAll} disabled={selectableVisible.length === 0}>
            Select All
          </button>
          <button className="btn-secondary" onClick={deselectAll} disabled={selectedCount === 0}>
            Deselect All
          </button>
        </div>
      </div>

      {/* Channel grid */}
      {isLoading && (
        <div className="py-16 flex justify-center text-on-surface-variant">
          <Loader2 size={24} className="animate-spin" />
        </div>
      )}
      {isError && (
        <div className="py-16 text-center text-on-surface-variant">
          Could not load Pluto TV channels. Try again later.
        </div>
      )}
      {!isLoading && !isError && filtered.length === 0 && (
        <div className="py-16 text-center text-on-surface-variant">No channels match your filters.</div>
      )}

      {!isLoading && !isError && filtered.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-gutter">
          {filtered.map((c) => {
            const added = isImported(c);
            const checked = selected.has(c.id);
            return (
              <div
                key={c.id}
                onClick={() => !added && toggle(c.id)}
                className={clsx(
                  "bg-surface-container-low border p-md flex items-start gap-3 transition-all",
                  added
                    ? "opacity-50 border-outline-variant"
                    : clsx(
                        "cursor-pointer hover:border-primary",
                        checked ? "border-primary-fixed-dim" : "border-outline-variant"
                      )
                )}
              >
                <ChannelLogo logo={c.logo} />
                <div className="flex-1 min-w-0">
                  <p className="font-bold text-body-sm truncate" title={c.name}>{c.name}</p>
                  <span className="inline-block mt-1.5 border border-outline-variant px-2 py-0.5 text-[10px] font-code-label uppercase tracking-wider text-on-surface-variant">
                    {c.category}
                  </span>
                </div>
                <div className="shrink-0">
                  {added ? (
                    <span className="badge-green text-[10px] whitespace-nowrap">Already Added</span>
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
  );
}

function ChannelLogo({ logo }: { logo: string }) {
  const [failed, setFailed] = useState(false);
  if (!logo || failed) {
    return (
      <div className="w-12 h-12 shrink-0 bg-surface-container border border-outline-variant rounded flex items-center justify-center">
        <MIcon name="live_tv" size={18} className="text-on-surface-variant opacity-50" />
      </div>
    );
  }
  return (
    <img
      src={logo}
      alt=""
      onError={() => setFailed(true)}
      className="w-12 h-12 shrink-0 object-contain bg-surface-container-highest border border-outline-variant rounded p-1"
    />
  );
}
