import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Crown, UploadCloud, Download } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { PlaylistCard, ChannelsModal, type Playlist } from "./Playlists";
import { PremiumEmpty, type PremiumSummary } from "../components/PremiumEmpty";

interface R2Backup { key: string; size: number; last_modified: string; url: string; }
interface ExportStatus { configured: boolean; backups: R2Backup[]; error?: string; }

/**
 * Premium → Playlists. The playlists that belong to the "Premium" bouquet
 * (resolved server-side by name). Reuses the same PlaylistCard and View modal as
 * the main Playlists page, including the Select-all / Import flow so premium
 * channels can be imported into Streams. Refresh/Delete are omitted here; live
 * per-channel probing is skipped since premium feeds may be IP-blocked.
 *
 * Also hosts the R2 backup controls: a daily background export runs server-side;
 * "Export now" triggers it on demand, and recent backups list with short-lived
 * presigned download links (so a private bucket still works).
 */
export default function PremiumPlaylists() {
  const qc = useQueryClient();
  const [viewing, setViewing] = useState<Playlist | null>(null);
  const [exporting, setExporting] = useState(false);

  const { data: playlists = [], isLoading } = useQuery<Playlist[]>({
    queryKey: ["premium-playlists"],
    queryFn: () => api.get("/premium/playlists").then((r) => r.data),
  });
  const { data: summary } = useQuery<PremiumSummary>({
    queryKey: ["premium-summary"],
    queryFn: () => api.get("/premium/summary").then((r) => r.data),
  });
  const { data: exportStatus } = useQuery<ExportStatus>({
    queryKey: ["premium-export"],
    queryFn: () => api.get("/premium/export").then((r) => r.data),
  });

  async function exportNow() {
    setExporting(true);
    const t = toast.loading("Exporting premium playlists to R2…");
    try {
      const r = await api.post("/premium/export").then((res) => res.data);
      toast.success(`Backed up ${r.count} file${r.count === 1 ? "" : "s"} to R2`, { id: t });
      qc.invalidateQueries({ queryKey: ["premium-export"] });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Export failed", { id: t });
    } finally {
      setExporting(false);
    }
  }

  const configured = exportStatus?.configured;
  const backups = exportStatus?.backups ?? [];

  return (
    <div className="p-lg space-y-md max-w-[1400px]">
      <div className="flex justify-between items-end flex-wrap gap-md">
        <div>
          <h2 className="text-lg font-bold tracking-tight mb-0.5 flex items-center gap-2">
            <Crown size={18} className="text-[#f5c86e]" /> Premium Playlists
          </h2>
          <p className="text-on-surface-variant text-[12px]">
            {isLoading ? "Loading…" : `${playlists.length} premium playlist${playlists.length === 1 ? "" : "s"}`}
          </p>
        </div>
        {configured && (
          <button className="btn-primary" onClick={exportNow} disabled={exporting}>
            {exporting ? <Loader2 size={16} className="animate-spin" /> : <UploadCloud size={16} />}
            Export to R2
          </button>
        )}
      </div>

      {configured === false && (
        <p className="flex items-center gap-1.5 text-[12px] text-on-surface-variant bg-surface-container rounded px-3 py-2 border border-outline-variant">
          R2 backup isn't configured yet — set the R2_* values in backend/.env to enable automatic daily exports.
        </p>
      )}

      {backups.length > 0 && (
        <div className="border border-outline-variant rounded-md divide-y divide-outline-variant overflow-hidden">
          <p className="px-3 py-2 text-[12px] font-bold bg-surface-container-low">Recent R2 backups</p>
          {backups.map((b) => (
            <a
              key={b.key}
              href={b.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-3 py-2 text-[12px] hover:bg-surface-container-low transition-colors"
            >
              <Download size={13} className="shrink-0 text-on-surface-variant" />
              <span className="font-mono truncate flex-1 min-w-0">{b.key}</span>
              <span className="shrink-0 text-on-surface-variant">{(b.size / 1024).toFixed(1)} KB</span>
            </a>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="py-16 flex justify-center text-on-surface-variant">
          <Loader2 size={24} className="animate-spin" />
        </div>
      ) : playlists.length === 0 ? (
        <PremiumEmpty summary={summary} kind="playlists" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-gutter">
          {playlists.map((p) => (
            <PlaylistCard key={p.id} playlist={p} onView={() => setViewing(p)} />
          ))}
        </div>
      )}

      {viewing && (
        <ChannelsModal
          playlist={viewing}
          onClose={() => setViewing(null)}
          channelsEndpoint={`/premium/playlists/${viewing.id}/channels`}
          skipProbe
        />
      )}
    </div>
  );
}
