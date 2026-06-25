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
    // invisible for live TV.
    backBufferLength: 90,
    maxBufferLength: 60,
    maxMaxBufferLength: 120,
    // Cap the buffer by *size* too (60 MB) so a high-bitrate source can't
    // balloon memory on low-end devices while still buffering plenty of time.
    maxBufferSize: 60 * 1000 * 1000,
    liveSyncDurationCount: 4,
    maxLiveSyncPlaybackRate: 1.5,

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
 * Jump a (live) video element to the freshest point it can play. Used when a
 * tab regains focus: a backgrounded live stream keeps draining its buffer, so
 * on return we skip the stale backlog straight to the live edge — exactly what
 * YouTube/Twitch do — instead of playing minutes-old footage.
 *
 * Safe for VOD too: it only ever moves forward toward the buffered end and
 * leaves a small safety margin so it doesn't land past what's decoded.
 */
export function resyncToLiveEdge(
  video: HTMLVideoElement,
  hls: { liveSyncPosition?: number | null } | null,
): void {
  try {
    const edge = hls?.liveSyncPosition;
    if (typeof edge === "number" && isFinite(edge) && edge > video.currentTime) {
      video.currentTime = edge;
      return;
    }
    if (video.buffered.length) {
      const end = video.buffered.end(video.buffered.length - 1);
      // Only resync if we're more than ~6s behind the buffered edge — avoids
      // pointless micro-seeks that would themselves cause a tiny rebuffer.
      if (end - video.currentTime > 6) video.currentTime = end - 0.5;
    }
  } catch {
    /* ignore — resync is best-effort */
  }
}
