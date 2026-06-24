import { useEffect, useRef, useState, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import Hls from "hls.js";
import {
  Play, Pause, Volume2, VolumeX, Maximize, Minimize,
  Loader2, AlertCircle, Settings, ChevronLeft, Gauge,
} from "lucide-react";

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export default function WatchPage() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const url = params.get("url") || "";
  const token = params.get("t") || "";
  const name = params.get("name") || "Stream";

  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);

  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [levels, setLevels] = useState<{ index: number; label: string; bitrate: number }[]>([]);
  const [currentLevel, setCurrentLevel] = useState(-1);
  const [showQuality, setShowQuality] = useState(false);
  const [showSpeed, setShowSpeed] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [seeking, setSeeking] = useState(false);
  const [showControls, setShowControls] = useState(true);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];

  // Init HLS
  useEffect(() => {
    const video = videoRef.current;
    if (!video || (!url && !token)) return;

    if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null; }
    setLoading(true); setError(null); setLevels([]); setCurrentLevel(-1);

    // Liveness watchdog handle (set up once hls.js is attached below).
    let watchdog: ReturnType<typeof setInterval> | null = null;

    // Encrypted play token (preferred): the upstream URL/creds stay server-side.
    // Fall back to a raw `url` param for older links.
    const hlsUrl = token
      ? `${window.location.origin}/live/t/${token}.m3u8`
      : url.startsWith("/") ? `${window.location.origin}${url}` : url;

    if (Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: true,
        // Deep buffer + sit further from the live edge so jitter on a long-haul
        // path is absorbed instead of stalling. Latency is invisible for live TV.
        backBufferLength: 90,
        maxBufferLength: 60,
        maxMaxBufferLength: 120,
        // Sit ~4 segments back: rides out jitter without falling off the back of
        // the server's rolling (delete_segments) window.
        liveSyncDurationCount: 4,
        maxLiveSyncPlaybackRate: 1.5,
        // Generous retry budget — a slow segment should wait/retry, not go fatal.
        manifestLoadingMaxRetry: 4,
        manifestLoadingRetryDelay: 1000,
        levelLoadingMaxRetry: 6,
        levelLoadingRetryDelay: 1000,
        fragLoadingMaxRetry: 8,
        fragLoadingRetryDelay: 1000,
      });
      hlsRef.current = hls;
      let recoverAttempts = 0;
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        const qs = [{ index: -1, label: "Auto", bitrate: 0 }];
        hls.levels
          .map((l, i) => ({
            index: i,
            label: l.height ? `${l.height}p` : `${Math.round((l.bitrate || 0) / 1000)}kbps`,
            bitrate: l.bitrate || 0,
          }))
          // Highest quality first so the menu reads Auto · 1080p · 720p · 480p.
          .sort((a, b) => b.bitrate - a.bitrate)
          .forEach((q) => qs.push(q));
        setLevels(qs);
        // Pre-buffer before the first play: hold playback until a cushion of
        // video is buffered ahead, so it opens smoothly instead of stalling on
        // the opening frames. hls.js keeps filling the buffer while paused. A
        // MAX_WAIT fallback starts anyway so a slow source never hangs on black.
        // Steady-state buffering + quality (Hls config above) are unchanged.
        const PREBUFFER_SECONDS = 6;
        const MAX_WAIT_MS = 8000;
        const waitStart = performance.now();
        const startWhenBuffered = () => {
          if (!videoRef.current) return;
          const r = video.buffered;
          const ready = r.length ? r.end(r.length - 1) - r.start(r.length - 1) : 0;
          if (ready >= PREBUFFER_SECONDS || performance.now() - waitStart > MAX_WAIT_MS) {
            setLoading(false);
            video.play().catch(() => {});
            setPlaying(true);
          } else {
            setLoading(true);
            window.setTimeout(startWhenBuffered, 250);
          }
        };
        startWhenBuffered();
      });

      hls.on(Hls.Events.LEVEL_SWITCHED, (_ev, data) => setCurrentLevel(data.level));

      hls.on(Hls.Events.FRAG_BUFFERED, () => {
        recoverAttempts = 0; // healthy again — refill the recovery budget
        setLoading(false);
      });

      hls.on(Hls.Events.ERROR, (_ev, data) => {
        if (!data.fatal) return;
        // Recover silently before surfacing an error — never die on a single
        // network/media blip. Re-buffer, don't stop. Give up only after budget.
        if (recoverAttempts < 6) {
          recoverAttempts++;
          setLoading(true);
          if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
          else hls.startLoad();
          return;
        }
        const msgs: Record<string, string> = { networkError: "Network error", mediaError: "Unsupported format", muxError: "Encoding error" };
        setError(msgs[data.type] || data.details);
        setLoading(false);
        hls.destroy();
        hlsRef.current = null;
      });

      // Liveness watchdog — some live/audio (radio) streams make hls.js quietly
      // stop fetching: it drains its buffer and freezes with no error event. If
      // the video isn't advancing while it should be playing, force a reload and
      // jump to the live edge so playback resumes instead of dying forever.
      let lastT = 0, stalls = 0;
      watchdog = setInterval(() => {
        const v = videoRef.current, h = hlsRef.current;
        if (!v || !h || v.paused || v.seeking || v.ended) { lastT = v ? v.currentTime : 0; stalls = 0; return; }
        if (v.currentTime > lastT + 0.1) { lastT = v.currentTime; stalls = 0; return; }
        if (++stalls < 2) return; // ~6s of no forward progress
        stalls = 0;
        try {
          h.startLoad();
          const edge = h.liveSyncPosition;
          if (typeof edge === "number" && isFinite(edge) && edge > v.currentTime) v.currentTime = edge;
          else if (v.buffered.length) { const e = v.buffered.end(v.buffered.length - 1); if (e > v.currentTime) v.currentTime = e - 0.3; }
          v.play().catch(() => {});
        } catch { /* ignore */ }
      }, 3000);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = hlsUrl;
      video.addEventListener("loadedmetadata", () => { setLoading(false); video.play().catch(() => {}); setPlaying(true); });
      video.addEventListener("error", () => { setError("Playback error"); setLoading(false); });
    } else {
      setError("HLS not supported in this browser");
      setLoading(false);
    }

    return () => { if (watchdog) clearInterval(watchdog); if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null; } };
  }, [url, token]);

  // Time update
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => { if (!seeking) setCurrentTime(v.currentTime); };
    const onDur = () => setDuration(v.duration || 0);
    const onEnd = () => setPlaying(false);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("loadedmetadata", onDur);
    v.addEventListener("ended", onEnd);
    return () => { v.removeEventListener("timeupdate", onTime); v.removeEventListener("loadedmetadata", onDur); v.removeEventListener("ended", onEnd); };
  }, [seeking]);

  // Fullscreen
  useEffect(() => {
    const h = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", h);
    return () => document.removeEventListener("fullscreenchange", h);
  }, []);

  // Speed
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed]);

  // Controls auto-hide
  const keepControls = useCallback(() => {
    setShowControls(true);
    if (hideTimer.current) clearTimeout(hideTimer.current);
    if (playing) hideTimer.current = setTimeout(() => setShowControls(false), 4000);
  }, [playing]);

  useEffect(() => { keepControls(); }, [playing]);

  const togglePlay = () => {
    const v = videoRef.current; if (!v) return;
    if (v.paused) { v.play(); setPlaying(true); } else { v.pause(); setPlaying(false); }
  };

  const toggleMute = () => {
    const v = videoRef.current; if (!v) return;
    v.muted = !v.muted; setMuted(v.muted);
  };

  const changeVolume = (val: number) => {
    const v = videoRef.current; if (!v) return;
    v.volume = val; setVolume(val); setMuted(val === 0);
  };

  const toggleFullscreen = () => {
    const el = containerRef.current; if (!el) return;
    if (!document.fullscreenElement) el.requestFullscreen().catch(() => {});
    else document.exitFullscreen().catch(() => {});
  };

  const seek = (e: React.MouseEvent | React.TouchEvent | React.ChangeEvent<HTMLInputElement>) => {
    const v = videoRef.current; if (!v) return;
    const target = e.target as HTMLInputElement;
    const t = parseFloat(target.value);
    v.currentTime = t;
    setCurrentTime(t);
    setSeeking(false);
  };

  const switchQuality = (index: number) => {
    if (hlsRef.current) { hlsRef.current.currentLevel = index; hlsRef.current.nextLevel = index; }
    setCurrentLevel(index); setShowQuality(false);
  };

  return (
    <div ref={containerRef} className="fixed inset-0 z-50 bg-black overflow-hidden"
      onMouseMove={keepControls}>

      {/* Video fills the whole container at a constant size; the control bars
          overlay on top (absolute) so showing/hiding them never resizes it. */}
      <video ref={videoRef} onClick={togglePlay}
        className="absolute inset-0 w-full h-full object-contain" playsInline muted={muted} />

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60">
          <div className="flex flex-col items-center gap-3">
            <Loader2 size={40} className="animate-spin text-white" />
            <p className="text-white/70">Loading stream…</p>
          </div>
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/80">
          <div className="flex flex-col items-center gap-3 px-6 text-center">
            <AlertCircle size={40} className="text-red-400" />
            <p className="text-white font-medium text-lg">Playback Error</p>
            <p className="text-white/60">{error}</p>
            <button onClick={() => { setError(null); setLoading(true); }} className="mt-2 px-6 py-2.5 bg-white/10 hover:bg-white/20 text-white rounded-lg transition-colors">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Top bar (overlay) */}
      {showControls && (
        <div className="absolute top-0 left-0 right-0 flex items-center gap-4 px-4 py-3 bg-gradient-to-b from-black/80 to-transparent">
          <button onClick={() => nav(-1)} className="text-white/80 hover:text-white transition-colors" title="Back">
            <ChevronLeft size={24} />
          </button>
          <h1 className="text-white font-medium text-lg truncate flex-1">{name}</h1>
        </div>
      )}

      {/* Bottom control bar (overlay) */}
      {showControls && !loading && (
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/95 to-transparent">
          {/* Progress bar */}
          <div className="px-4">
            <input
              type="range" min="0" max={duration || 0} step="0.1" value={currentTime}
              onMouseDown={() => setSeeking(true)}
              onMouseUp={seek}
              onTouchStart={() => setSeeking(true)}
              onTouchEnd={seek}
              onChange={(e) => { setSeeking(true); setCurrentTime(parseFloat(e.target.value)); }}
              className="w-full h-1 bg-white/20 rounded-full appearance-none cursor-pointer
                [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4
                [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:bg-red-500
                [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:cursor-pointer
                [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4
                [&::-moz-range-thumb]:bg-red-500 [&::-moz-range-thumb]:rounded-full
                [&::-moz-range-thumb]:border-0"
            />
          </div>

          {/* Buttons row */}
          <div className="flex items-center gap-3 px-4 pb-4 pt-1">
            {/* Play/Pause */}
            <button onClick={togglePlay} className="text-white hover:text-white/80 transition-colors">
              {playing ? <Pause size={26} /> : <Play size={26} />}
            </button>

            {/* Time */}
            <span className="text-white/80 text-xs tabular-nums min-w-[70px]">
              {formatTime(currentTime)} / {formatTime(duration)}
            </span>

            {/* Volume */}
            <div className="flex items-center gap-1.5">
              <button onClick={toggleMute} className="text-white hover:text-white/80 transition-colors">
                {muted || volume === 0 ? <VolumeX size={22} /> : <Volume2 size={22} />}
              </button>
              <input
                type="range" min="0" max="1" step="0.05" value={muted ? 0 : volume}
                onChange={(e) => changeVolume(parseFloat(e.target.value))}
                className="w-20 h-1 bg-white/30 rounded-full appearance-none cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3
                  [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white
                  [&::-webkit-slider-thumb]:rounded-full"
              />
            </div>

            <div className="flex-1" />

            {/* Speed selector */}
            <div className="relative">
              <button onClick={() => { setShowSpeed(!showSpeed); setShowQuality(false); }}
                className="flex items-center gap-1 text-white/80 hover:text-white text-xs font-medium px-2.5 py-1.5 rounded bg-white/10 hover:bg-white/20 transition-colors">
                <Gauge size={15} />
                {speed === 1 ? "Speed" : `${speed}x`}
              </button>
              {showSpeed && (
                <div className="absolute bottom-full right-0 mb-2 bg-[#1a1a2e] border border-white/10 rounded-lg overflow-hidden shadow-xl min-w-[80px]">
                  {SPEEDS.map((s) => (
                    <button key={s}
                      onClick={() => { setSpeed(s); setShowSpeed(false); }}
                      className={`w-full text-left px-3 py-2 text-xs transition-colors
                        ${s === speed ? "bg-indigo-600/40 text-white" : "text-white/70 hover:bg-white/10 hover:text-white"}`}>
                      {s === 1 ? "Normal" : `${s}x`}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Quality selector — only when the stream actually offers multiple
                renditions (Auto + ≥2). Channels now serve a single constant
                rendition, so this stays hidden rather than showing a useless
                "Auto / 0kbps" menu. */}
            {levels.length > 2 && (
              <div className="relative">
                <button onClick={() => { setShowQuality(!showQuality); setShowSpeed(false); }}
                  className="flex items-center gap-1 text-white/80 hover:text-white text-xs font-medium px-2.5 py-1.5 rounded bg-white/10 hover:bg-white/20 transition-colors">
                  <Settings size={15} />
                  {levels.find(l => l.index === currentLevel)?.label || "Auto"}
                </button>
                {showQuality && (
                  <div className="absolute bottom-full right-0 mb-2 bg-[#1a1a2e] border border-white/10 rounded-lg overflow-hidden shadow-xl min-w-[120px]">
                    {levels.map((l) => (
                      <button key={l.index}
                        onClick={() => switchQuality(l.index)}
                        className={`w-full text-left px-3 py-2 text-xs transition-colors
                          ${l.index === currentLevel ? "bg-indigo-600/40 text-white" : "text-white/70 hover:bg-white/10 hover:text-white"}`}>
                        {l.label}{l.bitrate > 0 && <span className="text-white/40 ml-2">{Math.round(l.bitrate / 1000)}k</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Fullscreen */}
            <button onClick={toggleFullscreen} className="text-white hover:text-white/80 transition-colors ml-1">
              {fullscreen ? <Minimize size={22} /> : <Maximize size={22} />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
