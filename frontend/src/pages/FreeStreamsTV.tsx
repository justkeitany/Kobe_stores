import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { MIcon } from "../components/MIcon";
import clsx from "clsx";

// Provider key -> display label + the category streams are imported into.
const PROVIDERS: Record<string, { label: string; category: string }> = {
  plex: { label: "Plex", category: "Plex TVs" },
  samsung: { label: "Samsung TV Plus", category: "Samsung TV Plus" },
  roku: { label: "Roku", category: "Roku TVs" },
  tubi: { label: "Tubi", category: "Tubi TVs" },
};

interface FreeChannel {
  id: string;
  name: string;
  category: string;
  logo: string;
  url: string;
}

interface DirectoryResponse {
  provider: string;
  name: string;
  channels: FreeChannel[];
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

export default function FreeStreamsTV() {
  const { provider = "" } = useParams();
  const cfg = PROVIDERS[provider];

  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  const { data: channels = [], isLoading, isError } = useQuery<FreeChannel[]>({
    queryKey: ["freestreams-channels", provider],
    queryFn: () =>
      api.get(`/freestreams/${provider}/channels`).then((r) => (r.data as DirectoryResponse).channels),
    staleTime: 10 * 60_000,
    enabled: !!cfg,
  });

  // Existing streams — used to flag already-imported channels (matched by URL).
  const { data: streams = [] } = useQuery<Stream[]>({
    queryKey: ["streams"],
    queryFn: () => api.get("/streams").then((r) => r.data),
  });

  const importedUrls = useMemo(() => {
    const urls = new Set<string>();
    for (const s of streams) if (s.stream_url) urls.add(s.stream_url);
    return urls;
  }, [streams]);

  const isImported = (c: FreeChannel) => importedUrls.has(c.url);

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
    if (!cfg) return;
    const toImport = channels.filter((c) => selected.has(c.id) && !isImported(c));
    if (!toImport.length) return;
    setImporting(true);
    const t = toast.loading(`Importing ${toImport.length} channel${toImport.length === 1 ? "" : "s"}…`);
    try {
      // 1. Ensure the provider's category exists.
      const cats: Category[] = await api.get("/categories").then((r) => r.data);
      let cat = cats.find((c) => c.name === cfg.category);
      if (!cat) {
        cat = await api.post("/categories", { name: cfg.category }).then((r) => r.data);
      }

      // 2. Create one stream per selected channel.
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
            // channel-id/tvg-id from the playlist; matches the i.mjh.nz EPG
            // (Plex/Samsung/Roku) so guide data binds to the channel. Skip the
            // URL fallback ids (only real channel ids will line up with EPG).
            epg_channel_id: c.id && !c.id.startsWith("http") ? c.id : null,
          });
          ok++;
        } catch {
          /* skip individual channel failures, keep importing the rest */
        }
      }

      toast.success(`Imported ${ok} channels to ${cfg.category} successfully`, { id: t });
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["streams"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Import failed", { id: t });
    } finally {
      setImporting(false);
    }
  }

  if (!cfg) {
    return (
      <div className="p-lg text-on-surface-variant">Unknown free-streams provider.</div>
    );
  }

  return (
    <div className="p-lg space-y-md max-w-[1400px]">
      {/* Header */}
      <div className="flex justify-between items-end flex-wrap gap-md">
        <div>
          <h2 className="text-lg font-bold tracking-tight mb-0.5">{cfg.label}</h2>
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
        <div className="relative flex-1 min-w-[200px] max-w-[20rem]">
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
          Could not load {cfg.label} channels. Try again later.
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
                <ChannelLogo logo={c.logo} name={c.name} />
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
