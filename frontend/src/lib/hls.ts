import Hls from "hls.js";
import type { HlsConfig } from "hls.js";

/**
 * Shared, production-tuned hls.js configuration for the web players.
 *
 * Both players (the embedded <Player> and the full-screen Watch page) used to
 * carry their own near-identical config blocks. They now share this one so
 * tuning happens in a single place and never drifts apart.
 *
 * The tuning goals, in order: (1) start fast, (2) never stall visibly, (3)
 * pick the right quality for the screen + connection — i.e. behave like
 * YouTube/Netflix-class players, which silently re-buffer and adapt instead of
 * erroring out.
 */

/**
 * Best-effort read of the connection's downlink (Mbps) via the Network
 * Information API. Lets ABR seed a sane bitrate estimate on the very first
 * segment instead of blindly guessing, so a slow phone doesn't open on the top
 * rung and choke. Falls back to a conservative 1.5 Mbps when unavailable
 * (Safari/Firefox don't expose it).
 */
function initialBandwidthEstimate(): number {
  try {
    const c = (navigator as unknown as {
      connection?: { downlink?: number };
    }).connection;
    if (c && typeof c.downlink === "number" && c.downlink > 0) {
      // downlink is in Mbps; use ~80% of it as the starting bits/sec estimate
      // to leave headroom for overhead and short-term dips.
      return Math.round(c.downlink * 1_000_000 * 0.8);
    }
  } catch {
    /* Network Information API unavailable — fall through */
  }
  return 1_500_000;
}

/**
 * Build the hls.js config. The result is intentionally a plain object so each
 * player can spread it and override a field if it ever needs to.
 */
export function makeHlsConfig(): Partial<HlsConfig> {
  const est = initialBandwidthEstimate();
  return {
    enableWorker: true,
    lowLatencyMode: false,

    // ---- Buffer policy -------------------------------------------------
    // Deep buffer + sit a few segments back from the live edge so jitter on a
    // long-haul path is absorbed instead of stalling. The extra latency is
    // invisible for live TV — Netflix/YouTube deliberately trade latency for
    // smoothness, and so do we.
    backBufferLength: 90,
    maxBufferLength: 60,
    maxMaxBufferLength: 120,
    // Cap the buffer by *size* too (60 MB) so a high-bitrate source can't
    // balloon memory on low-end devices while still buffering plenty of time.
    maxBufferSize: 60 * 1000 * 1000,
    // The real anti-stall lever for LIVE: you can't buffer ahead of the live
    // edge (that media doesn't exist yet), so the only way to get a forward
    // cushion is to sit further back. 6 segments (~12s at 2s segments) gives a
    // 12s jitter cushion and still fits inside the 24s/12-segment playlist
    // window the origin serves.
    liveSyncDurationCount: 6,
    // NB: liveMaxLatencyDurationCount is intentionally LEFT UNSET (defaults to
    // Infinity). Setting it makes hls.js *seek/jump* back toward the live edge
    // whenever you drift past it — on a dipping connection that means constant
    // skip-aheads, which is the opposite of smooth. We let latency drift and
    // rely on gentle rate catch-up + the visibility/online resync handlers.
    // Gentle catch-up only: nudge playbackRate to at most 1.1x (near-invisible)
    // to ease back toward the sync point, instead of a jarring 1.5x fast-forward.
    maxLiveSyncPlaybackRate: 1.1,

    // Warm the pipe: fetch the first fragment while the manifest is still being
    // parsed so the opening frames are ready sooner (snappier channel zap).
    startFragPrefetch: true,

    // ---- Automatic stall / gap recovery --------------------------------
    // hls.js's own gap-jumping. Small holes (e.g. a missing frame at a segment
    // boundary) are nudged over automatically instead of freezing — this is the
    // first line of defence; the interval watchdog in the players is the backup
    // for the rarer "silently stopped fetching" case.
    maxBufferHole: 0.5,
    highBufferWatchdogPeriod: 2,
    nudgeOffset: 0.2,
    nudgeMaxRetry: 6,

    // ---- Adaptive bitrate (ABR) ----------------------------------------
    startLevel: -1, // let ABR choose the opening rung
    // Don't fetch a rung larger than the player's pixels — playing 1080p into a
    // 480px box just wastes bandwidth and invites stalls. The single biggest
    // "be smart like YouTube" win.
    capLevelToPlayerSize: true,
    capLevelOnFPSDrop: true,
    abrEwmaDefaultEstimate: est,
    // React quickly to drops (fast EWMA short) but switch up conservatively
    // (slow EWMA long + sub-1 up-factor) so quality doesn't flap.
    abrEwmaFastLive: 3.0,
    abrEwmaSlowLive: 9.0,
    abrBandWidthFactor: 0.95,
    abrBandWidthUpFactor: 0.7,
    abrMaxWithRealBitrate: true,

    // ---- Retry budgets -------------------------------------------------
    // A slow segment should wait/retry, not go fatal.
    manifestLoadingMaxRetry: 4,
    manifestLoadingRetryDelay: 1000,
    levelLoadingMaxRetry: 6,
    levelLoadingRetryDelay: 1000,
    fragLoadingMaxRetry: 8,
    fragLoadingRetryDelay: 1000,
  };
}

/**
 * Make the live-edge distance segment-length-aware.
 *
 * The buffer config sits `liveSyncDurationCount` (6) SEGMENTS back from the live
 * edge — perfect for our own 2s-segment relay (≈12s back inside a 24s window).
 * But some upstreams (e.g. cdnlivetv) serve 10s segments in a ~6-segment window,
 * where 6 segments back is the WHOLE window: the playhead lands on the back edge
 * and falls off it every time the (laggy) window slides → the stream plays for a
 * few seconds, stalls, and loops, unable to advance.
 *
 * So once we know the real segment length, sit a fixed ~25-30s back instead —
 * which is ~2-3 long segments, leaving comfortable runway both ahead of the back
 * edge and behind the live edge to absorb the provider's bursty updates. Short
 * (2s) segments keep the default 6. hls.js reads `config.liveSyncDurationCount`
 * live on every tick, so mutating it here takes effect immediately.
 */
export function tuneLiveSyncForSegmentLength(hls: Hls): void {
  hls.on(Hls.Events.LEVEL_LOADED, (_e, data) => {
    const td = (data as { details?: { targetduration?: number } })?.details?.targetduration;
    if (typeof td !== "number" || td < 5) return; // normal short-segment live keeps the default
    const count = Math.max(2, Math.min(4, Math.round(28 / td)));
    if (hls.config.liveSyncDurationCount !== count) {
      hls.config.liveSyncDurationCount = count;
    }
  });
}

/**
 * Recover a (live) video element after a tab regains focus or the network comes
 * back, WITHOUT causing a needless rebuffer.
 *
 * Earlier this always skipped to the live edge when the playhead had drifted
 * behind ("be fresh like YouTube"). But a seek on a live stream ALWAYS forces a
 * visible rebuffer, so every refocus stalled a stream that was playing fine —
 * the "it stops and buffers when I come back to the window" complaint.
 *
 * New policy, matching this player's stated priority (smoothness > latency): if
 * the playhead is still inside a buffered range, do NOTHING — keep playing and
 * let hls.js's gentle 1.1x catch-up ease back toward live with no stall. Only
 * seek when the playhead has fallen OUT of the buffer (segments expired while
 * backgrounded), where a rebuffer is unavoidable anyway — then jump to the
 * freshest playable point.
 */
export function resyncToLiveEdge(
  video: HTMLVideoElement,
  hls: { liveSyncPosition?: number | null } | null,
): void {
  try {
    const t = video.currentTime;
    // Still inside buffered media? Leave it alone — no seek, no rebuffer.
    for (let i = 0; i < video.buffered.length; i++) {
      if (t >= video.buffered.start(i) - 0.1 && t <= video.buffered.end(i) + 0.1) {
        return;
      }
    }
    // Playhead is past/before the buffer (data gone): move to the freshest
    // playable point. A rebuffer here is unavoidable — the media we were on no
    // longer exists.
    const edge = hls?.liveSyncPosition;
    if (typeof edge === "number" && isFinite(edge) && edge > t) {
      video.currentTime = edge;
      return;
    }
    if (video.buffered.length) {
      const end = video.buffered.end(video.buffered.length - 1);
      if (end > t) video.currentTime = end - 0.5;
    }
  } catch {
    /* ignore — resync is best-effort */
  }
}
