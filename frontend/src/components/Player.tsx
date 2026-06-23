import { useEffect, useRef, useState, useCallback } from "react";
import Hls from "hls.js";
import {
  Play, Pause, Volume2, VolumeX, Maximize, Minimize,
  Loader2, AlertCircle, Settings,
} from "lucide-react";

interface QualityLevel {
  index: number;
  height: number;
  bitrate: number;
  label: string;
}

export default function Player({ url, title }: { url: string; title?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);

  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [levels, setLevels] = useState<QualityLevel[]>([]);
  const [currentLevel, setCurrentLevel] = useState(-1); // -1 = auto
  const [showQuality, setShowQuality] = useState(false);
  const [showControls, setShowControls] = useState(true);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>();

  // Initialize HLS
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !url) return;

    // Cleanup previous
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    setLoading(true);
    setError(null);
    setLevels([]);
    setCurrentLevel(-1);

    const isNativeHls = video.canPlayType("application/vnd.apple.mpegurl");
    const hlsUrl = url.startsWith("/") ? `${window.location.origin}${url}` : url;

    if (Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 90,
      });
      hlsRef.current = hls;

      hls.loadSource(hlsUrl);
      hls.attachMedia(video);

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        const qs: QualityLevel[] = hls.levels.map((l, i) => ({
          index: i,
          height: l.height || 0,
          bitrate: l.bitrate || 0,
          label: l.height ? `${l.height}p` : `${Math.round((l.bitrate || 0) / 1000)}kbps`,
        }));
        // Add auto option
        qs.unshift({ index: -1, height: 0, bitrate: 0, label: "Auto" });
        setLevels(qs);
        setLoading(false);
        video.play().catch(() => {});
        setPlaying(true);
      });

      hls.on(Hls.Events.ERROR, (_ev, data) => {
        if (data.fatal) {
          const errMap: Record<string, string> = {
            networkError: "Network error — check your connection",
            mediaError: "Stream format not supported",
            muxError: "Stream encoding error",
          };
          setError(errMap[data.type] || `Playback error: ${data.details}`);
          setLoading(false);
          hls.destroy();
          hlsRef.current = null;
        }
      });

      hls.on(Hls.Events.LEVEL_SWITCHED, (_ev, data) => {
        setCurrentLevel(data.level);
      });
    } else if (isNativeHls) {
      // Safari — native HLS support
      video.src = hlsUrl;
      video.addEventListener("loadedmetadata", () => {
        setLoading(false);
        video.play().catch(() => {});
        setPlaying(true);
      });
      video.addEventListener("error", () => {
        setError("Playback error — stream may be unavailable");
        setLoading(false);
      });
    } else {
      setError("HLS playback not supported in this browser");
      setLoading(false);
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [url]);

  // Fullscreen change listener
  useEffect(() => {
    const handler = () => {
      setFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  // Auto-hide controls
  const showControlsTemp = useCallback(() => {
    setShowControls(true);
    clearTimeout(hideTimer.current);
    if (playing) {
      hideTimer.current = setTimeout(() => setShowControls(false), 3000);
    }
  }, [playing]);

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) { v.play(); setPlaying(true); }
    else { v.pause(); setPlaying(false); }
  };

  const toggleMute = () => {
    const v = videoRef.current;
    if (!v) return;
    v.muted = !v.muted;
    setMuted(v.muted);
  };

  const changeVolume = (val: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.volume = val;
    setVolume(val);
    setMuted(val === 0);
  };

  const toggleFullscreen = () => {
    const el = containerRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  const switchQuality = (index: number) => {
    const hls = hlsRef.current;
    if (!hls) return;
    hls.currentLevel = index;
    hls.nextLevel = index;
    setCurrentLevel(index);
    setShowQuality(false);
  };

  return (
    <div
      ref={containerRef}
      className="relative bg-black rounded-lg overflow-hidden group w-full max-w-4xl mx-auto"
      onMouseMove={showControlsTemp}
      onMouseLeave={() => playing && setShowControls(false)}
      style={{ aspectRatio: "16/9" }}
    >
      {/* Video */}
      <video
        ref={videoRef}
        className="w-full h-full object-contain"
        onClick={togglePlay}
        playsInline
        muted={muted}
      />

      {/* Title bar */}
      {title && showControls && (
        <div className="absolute top-0 left-0 right-0 p-3 bg-gradient-to-b from-black/80 to-transparent">
          <p className="text-white text-sm font-medium truncate">{title}</p>
        </div>
      )}

      {/* Loading overlay */}
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60">
          <div className="flex flex-col items-center gap-3">
            <Loader2 size={32} className="animate-spin text-white" />
            <p className="text-white/70 text-sm">Loading stream…</p>
          </div>
        </div>
      )}

      {/* Error overlay */}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/80">
          <div className="flex flex-col items-center gap-3 text-center px-6">
            <AlertCircle size={36} className="text-red-400" />
            <p className="text-white font-medium">Playback Error</p>
            <p className="text-white/60 text-sm">{error}</p>
            <button
              onClick={() => { setError(null); setLoading(true); }}
              className="mt-2 px-4 py-2 bg-white/10 hover:bg-white/20 text-white rounded-lg text-sm transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Controls bar */}
      {showControls && !loading && (
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/90 to-transparent">
          <div className="flex items-center gap-3 px-4 py-3">
            {/* Play/Pause */}
            <button onClick={togglePlay} className="text-white hover:text-white/80 transition-colors">
              {playing ? <Pause size={22} /> : <Play size={22} />}
            </button>

            {/* Volume */}
            <div className="flex items-center gap-1.5">
              <button onClick={toggleMute} className="text-white hover:text-white/80 transition-colors">
                {muted || volume === 0 ? <VolumeX size={20} /> : <Volume2 size={20} />}
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

            {/* Quality selector */}
            {levels.length > 0 && (
              <div className="relative">
                <button
                  onClick={() => setShowQuality(!showQuality)}
                  className="flex items-center gap-1 text-white/80 hover:text-white text-xs font-medium px-2 py-1 rounded bg-white/10 hover:bg-white/20 transition-colors"
                >
                  <Settings size={14} />
                  {currentLevel === -1 ? "Auto" : levels.find(l => l.index === currentLevel)?.label || "Auto"}
                </button>
                {showQuality && (
                  <div className="absolute bottom-full right-0 mb-2 bg-[#1a1a2e] border border-white/10 rounded-lg overflow-hidden shadow-xl min-w-[120px]">
                    {levels.map((l) => (
                      <button
                        key={l.index}
                        onClick={() => switchQuality(l.index)}
                        className={`w-full text-left px-3 py-2 text-xs transition-colors
                          ${l.index === currentLevel
                            ? "bg-indigo-600/40 text-white"
                            : "text-white/70 hover:bg-white/10 hover:text-white"
                          }`}
                      >
                        {l.label}
                        {l.bitrate > 0 && <span className="text-white/40 ml-2">{Math.round(l.bitrate / 1000)}k</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Fullscreen */}
            <button onClick={toggleFullscreen} className="text-white hover:text-white/80 transition-colors">
              {fullscreen ? <Minimize size={20} /> : <Maximize size={20} />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
