import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Crown } from "lucide-react";
import api from "../lib/api";
import { PlaylistCard, ChannelsModal, type Playlist } from "./Playlists";
import { PremiumEmpty, type PremiumSummary } from "../components/PremiumEmpty";

/**
 * Premium → Playlists. Read-only view of the playlists that belong to the
 * "Premium" bouquet (resolved server-side by name). Reuses the same PlaylistCard
 * and View modal as the main Playlists page; Refresh/Delete are omitted here.
 */
export default function PremiumPlaylists() {
  const [viewing, setViewing] = useState<Playlist | null>(null);

  const { data: playlists = [], isLoading } = useQuery<Playlist[]>({
    queryKey: ["premium-playlists"],
    queryFn: () => api.get("/premium/playlists").then((r) => r.data),
  });
  const { data: summary } = useQuery<PremiumSummary>({
    queryKey: ["premium-summary"],
    queryFn: () => api.get("/premium/summary").then((r) => r.data),
  });

  return (
    <div className="p-lg space-y-md max-w-[1400px]">
      <div>
        <h2 className="text-lg font-bold tracking-tight mb-0.5 flex items-center gap-2">
          <Crown size={18} className="text-[#f5c86e]" /> Premium Playlists
        </h2>
        <p className="text-on-surface-variant text-[12px]">
          {isLoading ? "Loading…" : `${playlists.length} premium playlist${playlists.length === 1 ? "" : "s"}`}
        </p>
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
            <PlaylistCard key={p.id} playlist={p} onView={() => setViewing(p)} />
          ))}
        </div>
      )}

      {viewing && <ChannelsModal playlist={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
