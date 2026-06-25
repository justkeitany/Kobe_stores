import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Crown } from "lucide-react";
import toast from "react-hot-toast";
import api, { mintStreamToken } from "../lib/api";
import { type Channel } from "./Channels";
import { PremiumEmpty, type PremiumSummary } from "../components/PremiumEmpty";
import { LogoCard } from "../components/LogoCard";
import { Pagination } from "../components/Pagination";

const PAGE_SIZE = 36;

/**
 * Premium → Channels. The imported streams in the "Premium" bouquet's categories
 * (resolved server-side by bouquet name). Plays via the same token flow as the
 * main Channels page (mint token → /watch), so the full quality ladder + buffer
 * apply unchanged.
 */
export default function PremiumChannels() {
  const nav = useNavigate();
  const [page, setPage] = useState(0);

  const { data: channels = [], isLoading } = useQuery<Channel[]>({
    queryKey: ["premium-channels"],
    queryFn: () => api.get("/premium/channels").then((r) => r.data),
    refetchInterval: 60_000,
  });

  const pages = Math.max(1, Math.ceil(channels.length / PAGE_SIZE));
  const cur = Math.min(page, pages - 1);
  const shown = channels.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);
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
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-5">
            {shown.map((c) => (
              <LogoCard key={c.key} name={c.name} logo={c.logo} onClick={() => watch(c)} />
            ))}
          </div>
          <div className="pt-2">
            <Pagination page={cur + 1} totalPages={pages} onChange={(p) => setPage(p - 1)} />
          </div>
        </>
      )}
    </div>
  );
}
