import { Crown } from "lucide-react";

export interface PremiumSummary {
  has_bouquet: boolean;
  category_count: number;
  channel_count: number;
  playlist_count: number;
}

/** Empty / setup state shared by the Premium Channels and Playlists pages. */
export function PremiumEmpty({ summary, kind }: { summary?: PremiumSummary; kind: "channels" | "playlists" }) {
  const noBouquet = summary && !summary.has_bouquet;
  return (
    <div className="py-20 flex flex-col items-center text-center gap-3">
      <div className="w-12 h-12 rounded-full bg-surface-container flex items-center justify-center">
        <Crown size={22} className="text-on-surface-variant" />
      </div>
      <div>
        <p className="font-bold">No premium {kind}</p>
        <p className="text-on-surface-variant text-[13px] max-w-sm">
          {noBouquet
            ? "Create a bouquet named “Premium” (Bouquets page) and assign categories to it — its content appears here automatically."
            : kind === "playlists"
              ? "No saved playlist matches a premium category name yet."
              : "The Premium bouquet has no channels yet."}
        </p>
      </div>
    </div>
  );
}
