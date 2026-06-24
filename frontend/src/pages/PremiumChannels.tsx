import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Crown, Play } from "lucide-react";
import toast from "react-hot-toast";
import api, { mintStreamToken } from "../lib/api";
import { type Channel, type Health, BADGE, Logo } from "./Channels";
import { PremiumEmpty, type PremiumSummary } from "../components/PremiumEmpty";
import clsx from "clsx";

/**
 * Premium → Channels. The imported streams in the "Premium" bouquet's categories
 * (resolved server-side by bouquet name). Plays via the same token flow as the
 * main Channels page (mint token → /watch), so the full quality ladder + buffer
 * apply unchanged.
 */
export default function PremiumChannels() {
  const nav = useNavigate();

  const { data: channels = [], isLoading } = useQuery<Channel[]>({
    queryKey: ["premium-channels"],
    queryFn: () => api.get("/premium/channels").then((r) => r.data),
    refetchInterval: 60_000,
  });
  const { data: summary } = useQuery<PremiumSummary>({
    queryKey: ["premium-summary"],
    queryFn: () => api.get("/premium/summary").then((r) => r.data),
  });

  async function watch(c: Channel) {
    try {
      const token =
        c.imported && c.stream_id != null
          ? await mintStreamToken({ stream_id: c.stream_id })
          : c.url
          ? await mintStreamToken({ url: c.url })
          : null;
      if (token) nav(`/watch?t=${token}&name=${encodeURIComponent(c.name)}`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Couldn't open this channel");
    }
  }

  return (
    <div className="p-lg space-y-md max-w-[1500px]">
      <div>
        <h2 className="text-lg font-bold tracking-tight mb-0.5 flex items-center gap-2">
          <Crown size={18} className="text-[#f5c86e]" /> Premium Channels
        </h2>
        <p className="text-on-surface-variant text-[12px]">
          {isLoading ? "Loading…" : `${channels.length} premium channel${channels.length === 1 ? "" : "s"}`}
        </p>
      </div>

      {isLoading ? (
        <div className="py-16 flex justify-center text-on-surface-variant">
          <Loader2 size={24} className="animate-spin" />
        </div>
      ) : channels.length === 0 ? (
        <PremiumEmpty summary={summary} kind="channels" />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-2">
          {channels.map((c) => {
            const b = BADGE[c.health as Health];
            return (
              <div key={c.key} className="bg-surface-container-low border border-outline-variant rounded-md flex flex-col overflow-hidden">
                <div className="p-3 flex flex-col items-center text-center gap-1.5 flex-1">
                  <span className={clsx("self-start inline-flex items-center gap-1 text-[10px] font-medium rounded-full px-2 py-0.5", b.cls)}>
                    <span className={clsx("w-1.5 h-1.5 rounded-full", b.dot)} /> {b.label}
                  </span>
                  <Logo logo={c.logo} />
                  <p className="font-semibold text-[13px] leading-tight line-clamp-2">{c.name}</p>
                  <span className="text-[9px] font-code-label uppercase tracking-wider text-on-surface-variant border border-outline-variant rounded-full px-2 py-0.5 truncate max-w-full">
                    {c.source}
                  </span>
                </div>
                <div className="border-t border-outline-variant p-2">
                  <button
                    onClick={() => watch(c)}
                    className="w-full inline-flex items-center justify-center gap-1 text-[12px] font-medium rounded-md border border-primary/40 text-primary py-1.5 hover:bg-primary/10 transition-colors"
                  >
                    <Play size={13} /> Watch
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
