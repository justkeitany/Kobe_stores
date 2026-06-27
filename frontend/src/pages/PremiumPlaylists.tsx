import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Crown, Plus, RefreshCw } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { PlaylistCard, ChannelsModal, AddPlaylistModal, type Playlist } from "./Playlists";
import { PremiumEmpty, type PremiumSummary } from "../components/PremiumEmpty";

interface SyncResult {
  playlists: { name: string; updated: number; added: number; unchanged: number }[];
  restarted: number;
}

/**
 * Premium → Playlists. The playlists that belong to the "Premium" bouquet
 * (resolved server-side by name). Reuses the same PlaylistCard and View modal as
 * the main Playlists page, including the Select-all / Import flow so premium
 * channels can be imported into Streams.
 *
 * Source links are kept secret here (this is a distributable product): the M3U
 * badge is hidden on cards, no raw URLs are shown, and the R2 export is a hidden
 * background job — there's no download list in the UI. "Import playlist" adds a
 * new premium playlist from an M3U URL; "Sync channels" re-pulls each feed and
 * refreshes the imported streams in place (fixes stale/looping channels).
 */
export default function PremiumPlaylists() {
  const qc = useQueryClient();
  const [viewing, setViewing] = useState<Playlist | null>(null);
  const [importing, setImporting] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const { data: playlists = [], isLoading } = useQuery<Playlist[]>({
    queryKey: ["premium-playlists"],
    queryFn: () => api.get("/premium/playlists").then((r) => r.data),
  });
  const { data: summary } = useQuery<PremiumSummary>({
    queryKey: ["premium-summary"],
    queryFn: () => api.get("/premium/summary").then((r) => r.data),
  });

  function refreshPremium() {
    qc.invalidateQueries({ queryKey: ["premium-playlists"] });
    qc.invalidateQueries({ queryKey: ["premium-summary"] });
    qc.invalidateQueries({ queryKey: ["stream-urls"] });
  }

  async function syncChannels() {
    setSyncing(true);
    const t = toast.loading("Syncing premium channels…");
    try {
      const r = await api.post("/premium/sync").then((res) => res.data as SyncResult);
      const updated = r.playlists.reduce((n, p) => n + p.updated, 0);
      const added = r.playlists.reduce((n, p) => n + p.added, 0);
      toast.success(
        `Synced — ${updated} updated, ${added} added, ${r.restarted} restarted`,
        { id: t },
      );
      refreshPremium();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Sync failed", { id: t });
    } finally {
      setSyncing(false);
    }
  }

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
        <div className="flex items-center gap-2">
          <button className="btn-secondary" onClick={syncChannels} disabled={syncing}>
            {syncing ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            Sync channels
          </button>
          <button className="btn-primary" onClick={() => setImporting(true)}>
            <Plus size={16} /> Import playlist
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="py-16 flex justify-center text-on-surface-variant">
          <Loader2 size={24} className="animate-spin" />
        </div>
      ) : playlists.length === 0 ? (
        <PremiumEmpty summary={summary} kind="playlists" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-gutter">
          {playlists.map((p) => (
            <PlaylistCard key={p.id} playlist={p} onView={() => setViewing(p)} hideSourceBadge />
          ))}
        </div>
      )}

      {importing && (
        <AddPlaylistModal
          endpoint="/premium/playlists"
          onClose={() => setImporting(false)}
          onSaved={() => {
            setImporting(false);
            refreshPremium();
          }}
        />
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
