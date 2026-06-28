"""
FFmpeg restream manager.
- Starts FFmpeg on first viewer connection
- Stops when no viewers remain (saves CPU)
- Auto-restarts on crash
- Health checks every 30s
- HLS output (.m3u8 + .ts segments)
"""
import asyncio
import os
import shutil
import subprocess
import time
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, Optional
from app.config import settings
from app.pluto_stream import resolve as resolve_pluto_url
from app import proxy_resolver

try:
    import psutil  # CPU guardrail for ABR transcoding
except ImportError:  # pragma: no cover - psutil is optional
    psutil = None

logger = logging.getLogger(__name__)


# Input-side buffering applied to every live FFmpeg pull. Larger probe/analyze
# windows and a deep thread queue absorb brief source hiccups, regenerated PTS
# and discarded corrupt packets keep the muxer from stalling, and -re paces the
# input at native rate. These are all input options, placed before -i.
FFMPEG_INPUT_BUFFER_ARGS = [
    "-fflags", "+genpts+discardcorrupt+nobuffer",
    "-analyzeduration", "1000000",
    "-probesize", "1000000",
    "-thread_queue_size", "4096",
    "-re",
]


# HTTP source-resilience args for the live pull. Direct CDN sources (Pluto,
# Samsung) are stable, but imported playlist channels are M3USe links that
# 302-redirect to flaky upstreams (filmon, YouTube, Xtream) with short-lived
# per-request tokens. Those sources drop, 404 a segment when a token rotates, or
# signal EOF on a live playlist — and plain `-reconnect` doesn't cover EOF or
# HTTP errors, so FFmpeg exits rc=0 on the first hiccup → the manager cold-starts
# it → segment wipe + re-probe → the buffering spiral. These keep one FFmpeg
# alive across the hiccup instead. All are http-protocol INPUT options and MUST
# precede -i. A browser UA also stops upstreams that throttle FFmpeg's default
# "Lavf/…" agent. Harmless on the stable CDN sources.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

FFMPEG_HTTP_RESILIENCE_ARGS = [
    "-user_agent", _BROWSER_UA,
    "-reconnect", "1",
    "-reconnect_streamed", "1",
    # NOTE: deliberately NOT -reconnect_at_eof — it conflicts with the HLS
    # demuxer (segment byte-streams EOF normally; reconnecting them throws
    # "parse_playlist error / Immediate exit requested" and kills the stream).
    "-reconnect_on_network_error", "1",
    "-reconnect_on_http_error", "4xx,5xx",
    "-reconnect_delay_max", "5",
    # 15s I/O ceiling: a hung segment read fails fast and reconnects instead of
    # stalling the muxer indefinitely (microseconds).
    "-rw_timeout", "15000000",
    # Reuse the HTTP connection across segment fetches — lower per-segment latency.
    "-multiple_requests", "1",
]


# How long (seconds) a stream may go without a playlist request before it is
# considered idle and stopped. It must comfortably exceed a player's polling
# gap AND any transient buffering stall — a player that runs out of buffer stops
# polling the playlist while it rebuffers, so a window that's too short (8s, even
# 45s) reaps an *actively-watched* channel mid-stall. The reaped stream must then
# cold-restart, which takes longer than the original dip → a 1–5 min hang or a
# stream that never recovers (the "buffering death spiral"). The cost of being
# wrong is asymmetric: a too-short window kills live viewers, while a too-long one
# only wastes a little CPU keeping a truly-departed stream alive an extra minute.
# So bias long: 120s survives any realistic dip yet still frees CPU within ~2 min
# of a viewer actually leaving (the governor caps concurrent transcodes anyway).
STREAM_IDLE_TIMEOUT = 120


# Transcode ladder. "auto" copies the source codec untouched (no CPU, full
# bandwidth). The others scale down to a height cap and bound the bitrate so weak
# connections buffer less. scale=-2:H keeps the aspect ratio and an even width.
QUALITY_PROFILES: Dict[str, dict] = {
    "low":    {"height": 480,  "v_bitrate": "1000k", "maxrate": "1200k", "bufsize": "2400k", "a_bitrate": "96k"},
    "medium": {"height": 720,  "v_bitrate": "2500k", "maxrate": "3000k", "bufsize": "6000k", "a_bitrate": "128k"},
    "high":   {"height": 1080, "v_bitrate": "4500k", "maxrate": "5400k", "bufsize": "10800k", "a_bitrate": "160k"},
}
VALID_QUALITIES = {"auto", *QUALITY_PROFILES}


# Cooldown after a stream exhausts its retries on a dead/broken source, before
# any viewer poll is allowed to respawn it. Without this, a permanently-failing
# source (e.g. an offline provider) is restarted on every poll → a crash storm
# that pins CPU and spams "stream error" notifications.
ERROR_RETRY_COOLDOWN = 60


# Caches whether a source URL carries a video track. None is never stored — only
# definitive True/False — so a failed probe doesn't get memoised as a wrong answer.
_VIDEO_PROBE_CACHE: Dict[str, bool] = {}
# (has_video, height, bitrate) per resolved URL — drives both audio-only routing
# and the "is this source heavy enough to bother transcoding?" decision.
_SOURCE_PROBE_CACHE: Dict[str, tuple] = {}
# First audio stream's codec per resolved URL — drives the AC-3→AAC fix below.
_AUDIO_CODEC_CACHE: Dict[str, str] = {}
# Audio codecs browsers/MSE (hls.js) can decode natively in HLS. Anything else
# (ac3/eac3 Dolby, mp2, dts…) plays as SILENT video in the web player even though
# VLC/ffmpeg handle it — so we transcode just the audio to AAC (video still copy).
_BROWSER_SAFE_AUDIO = {"aac", "mp3"}


async def _probe_source(url: str) -> tuple:
    """Probe a source once: returns (has_video, height, bitrate).

    Used to (a) route audio-only sources to the audio pipeline and (b) decide
    whether a source is large enough to benefit from the transcode ladder. On any
    probe failure we assume a normal video source (True, 0, 0) so a transient blip
    never mis-routes a real channel. Cached per resolved URL.
    """
    resolved = resolve_pluto_url(url)
    if resolved in _SOURCE_PROBE_CACHE:
        return _SOURCE_PROBE_CACHE[resolved]
    ffprobe = os.path.join(os.path.dirname(settings.FFMPEG_PATH), "ffprobe")
    result = (True, 0, 0)
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error",
            # Same browser UA as the real fetch — some origins stall on or reject
            # the default Lavf UA, which would make the probe time out and the
            # source fall back to passthrough even when it's a heavy 1080p feed.
            "-user_agent", _BROWSER_UA,
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,height:format=bit_rate",
            "-of", "default=noprint_wrappers=1",
            resolved,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=12)
        if proc.returncode == 0:
            text = out.decode(errors="replace")
            has_video = "codec_type=video" in text
            height = bitrate = 0
            for line in text.splitlines():
                k, _, v = line.partition("=")
                v = v.strip()
                if v in ("", "N/A"):
                    continue
                try:
                    if k == "height":
                        height = int(v)
                    elif k == "bit_rate":
                        bitrate = int(v)
                except ValueError:
                    pass
            result = (has_video, height, bitrate)
            _SOURCE_PROBE_CACHE[resolved] = result
        # rc != 0 → couldn't read the source; leave uncached, assume video.
    except (asyncio.TimeoutError, Exception):
        result = (True, 0, 0)
    return result


async def _probe_audio_codec(url: str) -> str:
    """First audio stream's codec_name (lowercased), '' if none/unknown. Cached.

    Used to decide whether the passthrough path must transcode audio to AAC for
    browser playback — see _BROWSER_SAFE_AUDIO. On any probe failure we return ''
    (→ no transcode, copy as before) so a transient blip never needlessly burns
    CPU re-encoding a stream that was already fine.
    """
    resolved = resolve_pluto_url(url)
    if resolved in _AUDIO_CODEC_CACHE:
        return _AUDIO_CODEC_CACHE[resolved]
    ffprobe = os.path.join(os.path.dirname(settings.FFMPEG_PATH), "ffprobe")
    codec = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error",
            "-user_agent", _BROWSER_UA,
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "csv=p=0",
            resolved,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=12)
        if proc.returncode == 0:
            codec = out.decode(errors="replace").strip().lower()
            _AUDIO_CODEC_CACHE[resolved] = codec
        # rc != 0 → leave uncached, treat as unknown (copy).
    except (asyncio.TimeoutError, Exception):
        codec = ""
    return codec


def _source_wants_ladder(height: int, bitrate: int) -> bool:
    """Whether a source is heavy enough to warrant the transcode ladder.

    Small / low-bitrate feeds (≤720p, ≤~3 Mbps) play markedly smoother passed
    straight through: no encoder lag (the transcoded rungs were falling ~10s
    behind the live edge) and no pointless upscaling (a 540p source was being
    blown up to 720p). Only genuinely heavy sources — 1080p+, or anything that
    wouldn't fit a ~4 Mbps line with headroom — get downscaled so weak viewers
    still have a low rung. This is what makes ordinary channels behave like the
    rock-solid low-bitrate Free streams.
    """
    if bitrate and bitrate > 3_000_000:
        return True
    if height and height >= 1080:
        return True
    return False


async def _source_has_video(url: str) -> bool:
    """Whether the source has at least one video stream (cached per resolved URL).

    Audio-only sources (radio / Icecast) have no video track, so the multi-variant
    video ladder ([0:v]split…scale) and any -vf scale would fail instantly with
    ffmpeg rc=1. We probe once with ffprobe and route audio-only sources to an
    audio pipeline instead. On any probe failure we assume video (the safe default)
    so a transient network blip never wrongly strips video from a real channel.
    """
    resolved = resolve_pluto_url(url)
    if resolved in _VIDEO_PROBE_CACHE:
        return _VIDEO_PROBE_CACHE[resolved]
    ffprobe = os.path.join(os.path.dirname(settings.FFMPEG_PATH), "ffprobe")
    has_video = True
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            resolved,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=12)
        if proc.returncode == 0:
            # rc 0 + "video" → has a video stream; rc 0 + empty → audio-only.
            has_video = b"video" in out
            _VIDEO_PROBE_CACHE[resolved] = has_video
        # rc != 0 → probe couldn't read the source; leave uncached, assume video.
    except (asyncio.TimeoutError, Exception):
        has_video = True
    return has_video


def _codec_args(quality: str) -> list[str]:
    """FFmpeg codec args for a quality tier — stream copy for 'auto', else x264/AAC transcode."""
    profile = QUALITY_PROFILES.get(quality)
    if not profile:
        return ["-c", "copy"]
    return [
        "-vf", f"scale=-2:{profile['height']}",
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main",
        "-b:v", profile["v_bitrate"], "-maxrate", profile["maxrate"], "-bufsize", profile["bufsize"],
        "-g", "48", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", profile["a_bitrate"], "-ac", "2",
    ]


# ── Adaptive bitrate (ABR) ──────────────────────────────────────────────────
# Used only for the per-viewer TS path when a stream's quality is "auto".
# Each viewer's delivered throughput is measured (drain rate of their TS pipe)
# and the rendition is re-selected every ABR_RECHECK_SECONDS. "high" is a pure
# passthrough (stream copy) and costs no CPU.
ABR_LADDER: Dict[str, Optional[dict]] = {
    # Sized so each tier comfortably fits the connection that selects it. medium
    # (720p ~1.45 Mbps peak) is the sweet spot for a ~2 Mbps line — 720p at this
    # bitrate looks sharper than a starved 1080p would. high is untouched source.
    "low":    {"height": 480, "v_bitrate": "600k",  "maxrate": "750k",  "bufsize": "1500k", "a_bitrate": "96k"},
    "medium": {"height": 720, "v_bitrate": "1250k", "maxrate": "1450k", "bufsize": "3000k", "a_bitrate": "128k"},
    "high":   None,  # passthrough — no transcode (delivers full source, e.g. 1080p)
}
# Rendition tiers, lowest → highest.
ABR_TIERS = ["low", "medium", "high"]
# Each tier's peak delivered rate (Mbps, video+audio). The selector picks the
# highest tier whose peak fits the viewer's measured throughput after headroom,
# so a 2 Mbps line gets 720p (not an oversized 2.5 Mbps stream that buffers).
ABR_RUNG_MBPS = {"low": 0.85, "medium": 1.6, "high": 4.0}
# Keep ~18% of the measured pipe free so it never saturates (jitter/overhead).
ABR_HEADROOM = 0.82
# First (quick) measurement acts as the "test segment"; then re-check every 30s.
ABR_PROBE_SECONDS = 5
ABR_RECHECK_SECONDS = 30
# Drain-rate is capped by the current rendition's own bitrate, so a transcoded
# viewer can't measure headroom above their tier. Every Nth re-check, step up one
# tier to re-probe true capacity (and to drop back toward passthrough, freeing
# CPU); if it can't be sustained the next absolute pick drops it again.
ABR_PROBE_UP_EVERY = 4
# CPU guardrails.
ABR_MAX_TRANSCODES = 3
ABR_CPU_LIMIT = 75.0
ABR_TRANSCODE_THREADS = 2

# ── Multi-variant HLS ───────────────────────────────────────────────────────
# When a stream's quality is "auto", the HLS path publishes ONE shared ladder of
# renditions and the player adapts itself (and exposes a manual quality menu).
# v0 is the source passthrough (no transcode); v1/v2 are downscaled. Measured at
# ~0.6 of one core per channel, so several can run at once. Index order here is
# the variant number (vN) FFmpeg emits.
MULTIVARIANT_RUNGS = [
    {"name": "Original", "bandwidth": 5000000, "resolution": None},        # v0 = copy
    {"name": "720p",     "bandwidth": 1600000, "resolution": "1280x720"},  # v1
    {"name": "480p",     "bandwidth": 800000,  "resolution": "854x480"},   # v2
]
# Cap concurrent adaptive channels so a burst can't saturate the CPU; beyond
# this a new auto channel falls back to a single passthrough stream.
ABR_MAX_MULTIVARIANT = 4


def _pick_abr_quality(mbps: Optional[float]) -> str:
    """Highest tier whose peak rate fits the measured throughput after headroom.

    None (no measurement yet) → passthrough. Falls back to the lowest tier when
    nothing fits, so a very slow line still gets the smallest stream.
    """
    if mbps is None:
        return "high"
    budget = mbps * ABR_HEADROOM
    for tier in reversed(ABR_TIERS):  # high → medium → low
        if ABR_RUNG_MBPS[tier] <= budget:
            return tier
    return ABR_TIERS[0]


def _step_up(quality: str) -> str:
    """Next tier up (clamped at the top), used by the periodic up-probe."""
    try:
        return ABR_TIERS[min(ABR_TIERS.index(quality) + 1, len(ABR_TIERS) - 1)]
    except ValueError:
        return "high"


def _abr_codec_args(quality: str) -> list[str]:
    """Codec args for an ABR rendition. Transcodes are capped to -threads 2 so a
    single job can't hog the box; 'high' (and anything unknown) is stream copy."""
    profile = ABR_LADDER.get(quality)
    if not profile:
        return ["-c", "copy"]
    return [
        "-threads", str(ABR_TRANSCODE_THREADS),
        "-vf", f"scale=-2:{profile['height']}",
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main",
        "-b:v", profile["v_bitrate"], "-maxrate", profile["maxrate"], "-bufsize", profile["bufsize"],
        "-g", "48", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", profile["a_bitrate"], "-ac", "2",
    ]


class TranscodeGovernor:
    """Caps concurrent ABR transcodes and refuses new ones when CPU is high.

    A background sampler keeps a cached CPU reading so try_acquire() never blocks
    the event loop. When psutil is unavailable, only the job-count cap applies.
    """

    def __init__(self, max_jobs: int = ABR_MAX_TRANSCODES, cpu_limit: float = ABR_CPU_LIMIT):
        self.max_jobs = max_jobs
        self.cpu_limit = cpu_limit
        self._active = 0
        self._mv_active = 0
        self._lock = asyncio.Lock()
        self._cpu = 0.0
        self._sampler: Optional[asyncio.Task] = None

    def start(self) -> None:
        if psutil is None:
            return
        if self._sampler is None or self._sampler.done():
            try:
                psutil.cpu_percent(interval=None)  # prime the first reading
            except Exception:
                pass
            self._sampler = asyncio.create_task(self._sample())

    async def _sample(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                self._cpu = psutil.cpu_percent(interval=None)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)

    async def try_acquire(self) -> bool:
        """Reserve a transcode slot; False if at the job cap or CPU is too high."""
        async with self._lock:
            if self._active >= self.max_jobs:
                return False
            if psutil is not None and self._cpu >= self.cpu_limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active > 0:
                self._active -= 1

    async def try_acquire_mv(self) -> bool:
        """Reserve an adaptive (multi-variant HLS) channel slot; False if at the
        channel cap or CPU is too high (caller then serves single passthrough)."""
        async with self._lock:
            if self._mv_active >= ABR_MAX_MULTIVARIANT:
                return False
            if psutil is not None and self._cpu >= self.cpu_limit:
                return False
            self._mv_active += 1
            return True

    async def release_mv(self) -> None:
        async with self._lock:
            if self._mv_active > 0:
                self._mv_active -= 1

    @property
    def active(self) -> int:
        return self._active


# Global governor shared by every ABR viewer.
transcode_governor = TranscodeGovernor()


class StreamProcess:
    def __init__(self, stream_id: int, sources: list[str], stream_name: str,
                 quality: str = "auto", proxy_country: Optional[str] = None,
                 allow_multivariant: bool = True, force_adaptive: bool = False):
        self.stream_id = stream_id
        self.quality = quality if quality in VALID_QUALITIES else "auto"
        # If False, this stream always uses single-rendition passthrough HLS
        # (stream-copy). Playlist proxy streams set this to avoid 65× CPU per
        # viewer from multi-variant transcoding.
        self.allow_multivariant = allow_multivariant
        # Force the adaptive ladder even when the source probe comes back empty
        # (provider blocks ffprobe). Set on premium TV channels — see
        # Stream.force_adaptive. Ignored for audio-only sources.
        self.force_adaptive = force_adaptive
        # Source's first audio codec, probed once per start(). Drives the AC-3→AAC
        # passthrough fix (empty = unknown → copy, the pre-existing behaviour).
        self.audio_codec = ""
        # ISO country code for proxy-assisted playlist resolution (None = off).
        self.proxy_country = proxy_country
        # FFmpeg input URL + proxy args, computed (async) once per start().
        self._resolved_input: str = ""
        self._pargs: list[str] = []
        # Adaptive HLS (one shared multi-variant ladder, player-driven). Decided
        # at first start(): only when quality is "auto" and the governor allows.
        self.multivariant: bool = False
        self._holds_mv: bool = False
        # Set at start() by an ffprobe: audio-only sources (radio) skip the video
        # ladder entirely and use a plain audio HLS command.
        self.audio_only: bool = False
        # Native height of the source (px), detected by the same start() probe.
        # Used to label the passthrough/top rung of the adaptive ladder by its
        # real resolution (e.g. "1080p") instead of a raw bitrate ("5000kbps").
        self.source_height: int = 0
        # When the stream last exhausted its retries (dead source); gates respawns.
        self.gave_up_at: Optional[datetime] = None
        # Ordered failover chain. FFmpeg pulls one source at a time; on crash the
        # health monitor advances to the next entry (wrapping around).
        self.sources: list[str] = [s for s in sources if s] or [""]
        self.source_index: int = 0
        self.stream_name = stream_name
        self.process: Optional[asyncio.subprocess.Process] = None
        # client_key -> last time we saw a playlist request from that viewer
        self.viewers: Dict[str, datetime] = {}
        self.retry_count: int = 0
        self.started_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.status: str = "idle"
        self._lock = asyncio.Lock()
        self._health_task: Optional[asyncio.Task] = None

    @property
    def current_url(self) -> str:
        return self.sources[self.source_index % len(self.sources)]

    def _advance_source(self) -> None:
        """Rotate to the next source in the failover chain (wraps around)."""
        if len(self.sources) > 1:
            self.source_index = (self.source_index + 1) % len(self.sources)
            logger.info(
                f"Stream {self.stream_id} failing over to source "
                f"{self.source_index + 1}/{len(self.sources)}: {self.current_url}"
            )

    @property
    def hls_dir(self) -> str:
        return os.path.join(settings.HLS_OUTPUT_DIR, str(self.stream_id))

    @property
    def hls_playlist(self) -> str:
        return os.path.join(self.hls_dir, "index.m3u8")

    @property
    def master_playlist(self) -> str:
        return os.path.join(self.hls_dir, "master.m3u8")

    def variant_playlist(self, v: int) -> str:
        return os.path.join(self.hls_dir, f"v{v}", "index.m3u8")

    def _reset_hls_dir(self) -> None:
        """Remove only the *other* mode's leftovers before (re)launch.

        Clears single-mode files when starting multi-variant and vice-versa, so a
        mode switch can't leave a mixed/stale playlist — but a same-mode restart
        keeps its existing segments, avoiding a visible gap for current viewers.
        """
        os.makedirs(self.hls_dir, exist_ok=True)
        try:
            if self.multivariant:
                stale = [os.path.join(self.hls_dir, "index.m3u8")]
                stale += [
                    os.path.join(self.hls_dir, f)
                    for f in os.listdir(self.hls_dir)
                    if f.startswith("seg") and f.endswith(".ts")
                ]
                for p in stale:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            else:
                for d in range(len(MULTIVARIANT_RUNGS)):
                    shutil.rmtree(os.path.join(self.hls_dir, f"v{d}"), ignore_errors=True)
                try:
                    os.remove(os.path.join(self.hls_dir, "master.m3u8"))
                except OSError:
                    pass
        except OSError:
            pass

    def _needs_audio_transcode(self) -> bool:
        """True when the source audio can't play in a browser (AC-3/MP2/…) and we
        must transcode it to AAC. Unknown ('') → False, so we copy as before."""
        c = (self.audio_codec or "").lower()
        return bool(c) and c not in _BROWSER_SAFE_AUDIO

    def _copy_audio_args(self) -> list[str]:
        """Audio codec args for the single-rendition passthrough path."""
        if self._needs_audio_transcode():
            return ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
        return ["-c:a", "copy"]

    def _build_multivariant_cmd(self) -> list[str]:
        """One FFmpeg producing a shared HLS ladder: v0 source copy + v1 720p +
        v2 480p, plus per-variant playlists. The player adapts across them."""
        for i in range(len(MULTIVARIANT_RUNGS)):
            os.makedirs(os.path.join(self.hls_dir, f"v{i}"), exist_ok=True)
        return [
            settings.FFMPEG_PATH,
            "-hide_banner",
            "-loglevel", "warning",
            *FFMPEG_INPUT_BUFFER_ARGS,
            *FFMPEG_HTTP_RESILIENCE_ARGS,
            "-i", self._resolved_input,
            "-filter_complex",
            "[0:v]split=2[a][b];[a]scale=-2:720[v720o];[b]scale=-2:480[v480o]",
            # v0 — source passthrough (video copy; audio copy unless it's a codec
            # browsers can't decode — then transcode just the audio to AAC so the
            # top rung isn't silently muted in the web player).
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v:0", "copy",
            *(["-c:a:0", "aac", "-b:a:0", "128k"] if self._needs_audio_transcode() else ["-c:a:0", "copy"]),
            # v1 — 720p, capped to fit ~2 Mbps lines. force_key_frames pins a
            # keyframe exactly every 2s (= hls_time) and sc_threshold 0 stops
            # libx264 inserting extra scene-cut keyframes; together they yield
            # clean, uniform 2.0s segments instead of ragged 1.0–2.3s ones (which
            # destabilise the player's buffer math and cause micro-stalls).
            "-map", "[v720o]", "-map", "0:a:0?",
            "-c:v:1", "libx264", "-preset", "ultrafast", "-threads", str(ABR_TRANSCODE_THREADS),
            "-force_key_frames:v:1", "expr:gte(t,n_forced*2)", "-sc_threshold:v:1", "0",
            "-b:v:1", "1250k", "-maxrate:v:1", "1450k", "-bufsize:v:1", "3000k",
            "-c:a:1", "aac", "-b:a:1", "128k",
            # v2 — 480p (same uniform-segment treatment)
            "-map", "[v480o]", "-map", "0:a:0?",
            "-c:v:2", "libx264", "-preset", "ultrafast", "-threads", str(ABR_TRANSCODE_THREADS),
            "-force_key_frames:v:2", "expr:gte(t,n_forced*2)", "-sc_threshold:v:2", "0",
            "-b:v:2", "600k", "-maxrate:v:2", "750k", "-bufsize:v:2", "1500k",
            "-c:a:2", "aac", "-b:a:2", "96k",
            "-flush_packets", "1",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "12",
            "-hls_flags", "delete_segments+append_list+omit_endlist",
            "-hls_segment_type", "mpegts",
            "-master_pl_name", "master.m3u8",
            "-hls_segment_filename", os.path.join(self.hls_dir, "v%v", "seg%d.ts"),
            "-var_stream_map", "v:0,a:0 v:1,a:1 v:2,a:2",
            os.path.join(self.hls_dir, "v%v", "index.m3u8"),
        ]

    def _build_audio_cmd(self) -> list[str]:
        """HLS command for an audio-only source (radio / Icecast).

        No video maps or filters (those would fail rc=1) — just a single AAC audio
        rendition, transcoded for player compatibility across MP3/AAC sources. It
        is served through the normal single-rendition playlist path.
        """
        os.makedirs(self.hls_dir, exist_ok=True)
        return [
            settings.FFMPEG_PATH,
            "-hide_banner",
            "-loglevel", "warning",
            *FFMPEG_INPUT_BUFFER_ARGS,
            *FFMPEG_HTTP_RESILIENCE_ARGS,
            "-i", self._resolved_input,
            "-vn",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-flush_packets", "1",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "12",
            "-hls_flags", "delete_segments+append_list+omit_endlist",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", os.path.join(self.hls_dir, "seg%d.ts"),
            self.hls_playlist,
        ]

    def _build_ffmpeg_cmd(self) -> list[str]:
        """
        Low-latency HLS optimized FFmpeg command.
        - 2s segments for low latency
        - copy codec to avoid re-encoding (zero transcoding delay)
        - delete old segments automatically
        """
        os.makedirs(self.hls_dir, exist_ok=True)
        return [
            settings.FFMPEG_PATH,
            "-hide_banner",
            "-loglevel", "warning",
            # Input-side buffering (probe/analyze window, thread queue, -re) and
            # HTTP source resilience — these are INPUT options so they must come
            # before -i (the reconnect flags used to sit after -i, where they
            # applied to the output and did nothing for a dropping source).
            *FFMPEG_INPUT_BUFFER_ARGS,
            *FFMPEG_HTTP_RESILIENCE_ARGS,
            # Proxy headers for 403-fallback streams (empty otherwise).
            *self._pargs,
            # Pluto channel URLs are rewritten to the jmp2.uk resolver, which
            # redirects to a working stream. Non-Pluto URLs pass through.
            "-i", self._resolved_input,
            # Stream copy ('auto') or transcode down to the selected quality tier.
            # In copy mode, video is still copied but audio is forced to AAC when
            # the source uses a browser-incompatible codec (AC-3/MP2/…).
            *(["-c:v", "copy", *self._copy_audio_args()]
              if _codec_args(self.quality) == ["-c", "copy"] else _codec_args(self.quality)),
            # HLS output — flush each packet and hold a deeper segment window so
            # players have more buffered ahead of the live edge.
            "-flush_packets", "1",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "12",
            "-hls_flags", "delete_segments+append_list+split_by_time+program_date_time",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", os.path.join(self.hls_dir, "seg%d.ts"),
            "-method", "PUT",
            self.hls_playlist,
        ]

    async def start(self) -> bool:
        async with self._lock:
            if self.status == "running":
                return True
            try:
                self.status = "starting"
                # Resolve the FFmpeg input once per start: Pluto rewrite + (for
                # proxy_country streams) fetch the playlist via the regional proxy
                # and use the resolved URL. _pargs is non-empty only in 403
                # fallback, where FFmpeg pulls everything through the proxy.
                self._resolved_input = await proxy_resolver.resolve_input(
                    self.current_url, self.stream_id, self.proxy_country
                )
                self._pargs = await proxy_resolver.proxy_args(
                    self.stream_id, self.proxy_country
                )
                # Decide the delivery shape for this source (kept across restarts):
                #   - audio-only (radio/Icecast) -> dedicated audio pipeline
                #     (the video ladder/scale would crash rc=1). Probed once.
                #   - heavy video (1080p+ or >3 Mbps) -> adaptive ladder so the
                #     viewer can drop to 720p/480p to beat buffering. The ladder
                #     rungs are real downscales (never upscaled — small sources
                #     are excluded below) and use the ultrafast encoder so they
                #     keep real-time and never lag behind the live edge.
                #   - everything else (small/low-bitrate) -> single passthrough
                #     copy (~0 CPU); transcoding it would only add lag.
                # Skip the probe for playlist streams (allow_multivariant=False) —
                # ffprobe can take 10+s on high-latency upstreams.
                self.audio_only = False
                wants_ladder = False
                if self.allow_multivariant or self.quality != "auto":
                    has_video, height, bitrate = await _probe_source(self._resolved_input)
                    self.audio_only = not has_video
                    self.source_height = height or 0
                    # Detect browser-incompatible audio (AC-3/MP2/…) so the copy
                    # paths transcode just the audio to AAC. Only for video sources
                    # — audio-only feeds use _build_audio_cmd which already → AAC.
                    self.audio_codec = "" if self.audio_only else await _probe_audio_codec(self._resolved_input)
                    wants_ladder = _source_wants_ladder(height, bitrate)
                    # Forced premium TV: engage the ladder even when the probe was
                    # blocked (0×0). The probe returns has_video=True on failure,
                    # so a genuinely audio-only source is still correctly excluded.
                    if self.force_adaptive and not self.audio_only:
                        wants_ladder = True
                        if not self.source_height:
                            self.source_height = 1080  # label top rung "1080p"
                    if self.allow_multivariant and self.quality == "auto" and not self.audio_only:
                        logger.info(
                            f"Stream {self.stream_id}: source {height or '?'}p "
                            f"{(bitrate // 1000) if bitrate else '?'}kbps -> "
                            f"{'adaptive ladder (1080/720/480)' if wants_ladder else 'passthrough (no transcode)'}"
                        )
                # Only spin up the (CPU-heavy) ladder for genuinely heavy sources,
                # and only while the governor has a slot free.
                if (self.quality == "auto" and self.allow_multivariant and wants_ladder
                        and not self._holds_mv and not self.audio_only):
                    transcode_governor.start()
                    if await transcode_governor.try_acquire_mv():
                        self.multivariant = True
                        self._holds_mv = True
                self._reset_hls_dir()
                if self.multivariant:
                    cmd = self._build_multivariant_cmd()
                elif self.audio_only:
                    cmd = self._build_audio_cmd()
                else:
                    cmd = self._build_ffmpeg_cmd()
                self.process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.started_at = datetime.now(timezone.utc)
                self.status = "running"
                self.last_error = None
                logger.info(f"Stream {self.stream_id} ({self.stream_name}) started, PID={self.process.pid}")

                # Start health monitor
                if self._health_task is None or self._health_task.done():
                    self._health_task = asyncio.create_task(self._health_monitor())
                return True
            except Exception as e:
                self.status = "error"
                self.last_error = str(e)
                logger.error(f"Failed to start stream {self.stream_id}: {e}")
                return False

    async def stop(self):
        async with self._lock:
            if self._health_task and not self._health_task.done():
                self._health_task.cancel()
                try:
                    await self._health_task
                except asyncio.CancelledError:
                    pass
            if self.process and self.process.returncode is None:
                try:
                    self.process.terminate()
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
            self.status = "stopped"
            self.process = None
            self.retry_count = 0
            logger.info(f"Stream {self.stream_id} stopped")
        # Release the adaptive-channel slot (outside self._lock — different lock).
        if self._holds_mv:
            await transcode_governor.release_mv()
            self._holds_mv = False
            self.multivariant = False

    async def _health_monitor(self):
        """Restart FFmpeg if it crashes while viewers are still watching.

        Idle stopping (no viewers) is handled by the manager-level reaper, not
        here — so this task never stops its own stream and never cancels itself.
        """
        while True:
            try:
                await asyncio.sleep(3)

                if self.process and self.process.returncode is not None:
                    returncode = self.process.returncode
                    err_text = ""
                    if self.process.stderr:
                        try:
                            stderr = await asyncio.wait_for(
                                self.process.stderr.read(2048), timeout=1
                            )
                            self.last_error = stderr.decode(errors="replace")
                            err_text = self.last_error
                        except asyncio.TimeoutError:
                            pass

                    # 403 on a segment CDN that IS geo-gated — promote to
                    # full-proxy routing so the next respawn pulls everything
                    # through the proxy. One-shot: fallback expires in 1 h.
                    if self.proxy_country and "403" in err_text and \
                       "Forbidden" in err_text and not self._pargs:
                        asyncio.create_task(
                            proxy_resolver.trip_fallback(self.stream_id)
                        )

                    logger.warning(
                        f"Stream {self.stream_id} crashed (rc={returncode}), "
                        f"retry {self.retry_count}/{settings.MAX_RETRY_ATTEMPTS}"
                    )
                    self.retry_count += 1

                    if self.retry_count > settings.MAX_RETRY_ATTEMPTS:
                        self.status = "error"
                        self.gave_up_at = datetime.now(timezone.utc)
                        logger.error(f"Stream {self.stream_id} exceeded max retries, giving up")
                        # Ask the AI assistant to diagnose (and auto-fix in autofix
                        # mode). Fire-and-forget; no-op when AI is off/keyless.
                        try:
                            from app.ai import diagnose_by_id
                            asyncio.create_task(diagnose_by_id(self.stream_id, self.last_error or ""))
                        except Exception:
                            pass
                        break

                    # Fail over to the next source before retrying, so a dead
                    # primary is abandoned immediately rather than retried in place.
                    self._advance_source()
                    self.status = "starting"
                    await asyncio.sleep(min(self.retry_count * 2, 30))
                    await self.start()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error for stream {self.stream_id}: {e}")
                await asyncio.sleep(5)

    def heartbeat(self, client_key: str):
        """Record that a viewer just requested the playlist (keeps stream alive)."""
        self.viewers[client_key] = datetime.now(timezone.utc)

    def active_viewers(self) -> int:
        """Number of viewers seen within the idle window (prunes stale ones)."""
        now = datetime.now(timezone.utc)
        self.viewers = {
            k: v for k, v in self.viewers.items()
            if (now - v).total_seconds() <= STREAM_IDLE_TIMEOUT
        }
        return len(self.viewers)


class FFmpegManager:
    def __init__(self):
        self._streams: Dict[int, StreamProcess] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None

    def active_stream_count(self) -> int:
        """Streams currently running/starting — i.e. open upstream connections.

        Used by the playlist health features to back off: probing a channel opens
        another connection to the same provider, and connection-limited accounts
        (e.g. an M3USe trial) reject it with "multiple connections detected" and
        can disrupt the live stream. So health checks skip while anything plays.
        """
        return sum(1 for sp in self._streams.values() if sp.status in ("running", "starting"))

    def _ensure_reaper(self):
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reaper())

    async def _reaper(self):
        """Stop streams that have had no viewer heartbeat within the idle window."""
        while True:
            try:
                await asyncio.sleep(2)
                async with self._lock:
                    ids = list(self._streams.keys())
                for sid in ids:
                    async with self._lock:
                        sp = self._streams.get(sid)
                    if (
                        sp
                        and sp.status in ("running", "starting")
                        and sp.active_viewers() == 0
                    ):
                        logger.info(f"Stream {sid} idle (no viewers) — stopping")
                        await self.stop_stream(sid)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reaper error: {e}")
                await asyncio.sleep(2)

    async def get_or_create(
        self, stream_id: int, sources: list[str], stream_name: str,
        quality: str = "auto", proxy_country: Optional[str] = None,
        allow_multivariant: bool = True, force_adaptive: bool = False,
    ) -> StreamProcess:
        async with self._lock:
            sp = self._streams.get(stream_id)
            if sp is None:
                sp = StreamProcess(stream_id, sources, stream_name, quality,
                                   proxy_country, allow_multivariant, force_adaptive)
                self._streams[stream_id] = sp
            elif sp.status not in ("running", "starting"):
                # Pick up edited source pools / quality / proxy_country the next
                # time it (re)starts.
                if sources:
                    sp.sources = [s for s in sources if s] or [""]
                    sp.source_index = 0
                sp.quality = quality if quality in VALID_QUALITIES else "auto"
                sp.proxy_country = proxy_country
                sp.allow_multivariant = allow_multivariant
                sp.force_adaptive = force_adaptive
            return sp

    async def start_stream(
        self, stream_id: int, sources: list[str], stream_name: str,
        client_key: str = "viewer", quality: str = "auto",
        proxy_country: Optional[str] = None,
        allow_multivariant: bool = True, force_adaptive: bool = False,
    ) -> StreamProcess:
        self._ensure_reaper()
        sp = await self.get_or_create(stream_id, sources, stream_name, quality,
                                      proxy_country, allow_multivariant, force_adaptive)
        sp.heartbeat(client_key)
        # Channel switch: free this viewer's previous channel BEFORE starting the
        # new one, so a connection-limited upstream (M3USe trial) never sees two
        # connections at once. Done here instead of by lowering the idle window,
        # so the channel actually being watched keeps its full buffering grace.
        await self._release_other_streams(stream_id, client_key)
        if sp.status not in ("running", "starting"):
            # If it recently exhausted its retries on a dead/broken source, hold
            # off respawning until the cooldown passes — otherwise every viewer
            # poll relaunches a doomed ffmpeg (CPU storm + error-notification spam).
            if sp.status == "error" and sp.gave_up_at is not None:
                idle = (datetime.now(timezone.utc) - sp.gave_up_at).total_seconds()
                if idle < ERROR_RETRY_COOLDOWN:
                    return sp
            sp.retry_count = 0
            sp.gave_up_at = None
            await sp.start()
        return sp

    async def _release_other_streams(self, keep_id: int, client_key: str) -> None:
        """Stop other streams whose ONLY viewer is this client (a channel switch).

        Frees the upstream connection immediately so a connection-limited account
        doesn't trip "multiple connections". Never stops a stream that someone
        else is still watching.
        """
        async with self._lock:
            others = [(sid, sp) for sid, sp in self._streams.items() if sid != keep_id]
        for sid, sp in others:
            sp.active_viewers()  # prune stale heartbeats first
            if client_key in sp.viewers and len(sp.viewers) == 1:
                logger.info(f"Stream {sid} released — viewer switched to {keep_id}")
                await self.stop_stream(sid)

    async def stop_stream(self, stream_id: int):
        async with self._lock:
            sp = self._streams.pop(stream_id, None)
        if sp:
            await sp.stop()

    async def restart_stream(
        self, stream_id: int, sources: Optional[list[str]] = None, quality: Optional[str] = None,
        force_adaptive: Optional[bool] = None,
    ) -> bool:
        async with self._lock:
            sp = self._streams.get(stream_id)
        if not sp:
            return False
        await sp.stop()
        if sources:
            sp.sources = [s for s in sources if s] or [""]
        if quality is not None:
            sp.quality = quality if quality in VALID_QUALITIES else "auto"
        if force_adaptive is not None:
            sp.force_adaptive = force_adaptive
        sp.source_index = 0
        await asyncio.sleep(1)
        return await sp.start()

    async def get_status(self, stream_id: int) -> Optional[dict]:
        async with self._lock:
            sp = self._streams.get(stream_id)
        if not sp:
            return None
        return {
            "stream_id": stream_id,
            "status": sp.status,
            "quality": sp.quality,
            "viewer_count": sp.active_viewers(),
            "retry_count": sp.retry_count,
            "last_error": sp.last_error,
            "started_at": sp.started_at.isoformat() if sp.started_at else None,
            "active_source": sp.current_url,
            "active_source_index": sp.source_index,
            "source_count": len(sp.sources),
        }

    async def get_all_statuses(self) -> list[dict]:
        async with self._lock:
            stream_ids = list(self._streams.keys())
        return [s for sid in stream_ids if (s := await self.get_status(sid))]

    async def stop_all(self):
        async with self._lock:
            stream_ids = list(self._streams.keys())
        for sid in stream_ids:
            await self.stop_stream(sid)

    async def spawn_ts(self, url: str, quality: str = "auto") -> asyncio.subprocess.Process:
        """Start an FFmpeg that emits a continuous MPEG-TS stream on stdout.

        Used by the Xtream `.ts` output: one process per viewer remuxing ('auto')
        or transcoding the source into a single progressive TS stream, which
        players buffer more smoothly than HLS on weak connections. The caller
        owns the process and must kill it when the client disconnects.
        """
        cmd = [
            settings.FFMPEG_PATH,
            "-hide_banner",
            "-loglevel", "error",
            *FFMPEG_INPUT_BUFFER_ARGS,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", resolve_pluto_url(url),
            *_codec_args(quality),
            "-flush_packets", "1",
            "-f", "mpegts",
            "-",
        ]
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _spawn_abr(self, url: str, quality: str) -> asyncio.subprocess.Process:
        """FFmpeg emitting MPEG-TS on stdout for an ABR rendition (capped threads)."""
        cmd = [
            settings.FFMPEG_PATH,
            "-hide_banner",
            "-loglevel", "error",
            *FFMPEG_INPUT_BUFFER_ARGS,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", resolve_pluto_url(url),
            *_abr_codec_args(quality),
            "-flush_packets", "1",
            "-f", "mpegts",
            "-",
        ]
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _open_abr(self, url: str, desired: str):
        """Open a rendition, honouring the CPU governor.

        Transcoded tiers (low/medium) need a governor slot; if none is free (job
        cap hit or CPU too high) we silently fall back to passthrough so the
        viewer always gets video. Returns (proc, actual_quality, holds_slot).
        """
        if desired in ("low", "medium"):
            if await transcode_governor.try_acquire():
                try:
                    proc = await self._spawn_abr(url, desired)
                    return proc, desired, True
                except Exception:
                    await transcode_governor.release()
                    logger.warning("ABR transcode spawn failed for %s — passthrough", url)
        # Passthrough (high) or guardrail/spawn fallback.
        proc = await self._spawn_abr(url, "high")
        return proc, "high", False

    async def _close_abr(self, proc: Optional[asyncio.subprocess.Process], holds_slot: bool) -> None:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            except Exception:
                pass
        if holds_slot:
            await transcode_governor.release()

    async def abr_ts_stream(self, url: str) -> AsyncIterator[bytes]:
        """Per-viewer adaptive MPEG-TS stream (used when stream quality='auto').

        Measures delivered throughput (how fast the client drains the pipe) and
        re-picks the rendition every ~30s — upgrading when there's headroom,
        downgrading when the client can't keep up. CPU is protected by the
        governor (max jobs + CPU ceiling + threads cap). The transcode is killed
        the moment the client disconnects (generator close → finally). Any error
        falls back to passthrough; the viewer never sees a failure.
        """
        transcode_governor.start()
        # Start on passthrough: zero CPU until the first measurement says otherwise.
        proc, quality, holds_slot = await self._open_abr(url, "high")
        win_bytes = 0
        win_start = time.monotonic()
        next_eval = win_start + ABR_PROBE_SECONDS
        empty_restarts = 0
        eval_count = 0
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=20)
                except asyncio.TimeoutError:
                    break  # source stalled; let the player reconnect
                if not chunk:
                    # FFmpeg exited. Fall back to passthrough once or twice, then give up.
                    empty_restarts += 1
                    if empty_restarts > 2:
                        break
                    await self._close_abr(proc, holds_slot)
                    proc, quality, holds_slot = await self._open_abr(url, "high")
                    win_bytes = 0
                    win_start = time.monotonic()
                    next_eval = win_start + ABR_RECHECK_SECONDS
                    continue

                empty_restarts = 0
                win_bytes += len(chunk)
                yield chunk

                now = time.monotonic()
                if now >= next_eval:
                    elapsed = now - win_start
                    mbps = (win_bytes * 8) / (elapsed * 1_000_000) if elapsed > 0 else None
                    eval_count += 1
                    if quality != "high" and eval_count % ABR_PROBE_UP_EVERY == 0:
                        # Periodic up-probe to re-test true capacity above the cap.
                        target = _step_up(quality)
                    else:
                        target = _pick_abr_quality(mbps)
                    if target != quality:
                        await self._close_abr(proc, holds_slot)
                        proc, quality, holds_slot = await self._open_abr(url, target)
                        logger.info(
                            "ABR %s: %.2f Mbps measured → %s", url, mbps or 0.0, quality
                        )
                    win_bytes = 0
                    win_start = now
                    next_eval = now + ABR_RECHECK_SECONDS
        finally:
            await self._close_abr(proc, holds_slot)

    async def test_stream_url(self, url: str) -> dict:
        """Quick test to check if a stream URL is accessible."""
        try:
            cmd = [
                settings.FFMPEG_PATH,
                "-hide_banner", "-loglevel", "error",
                "-i", url,
                "-t", "3",
                "-f", "null", "-",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
                rc = proc.returncode
            except asyncio.TimeoutError:
                proc.kill()
                return {"alive": True, "message": "Stream reachable (timed out reading, which is normal)"}

            if rc == 0:
                return {"alive": True, "message": "Stream OK"}
            else:
                error = stderr.decode(errors="replace")[:500]
                return {"alive": False, "message": error}
        except Exception as e:
            return {"alive": False, "message": str(e)}


# Global singleton
ffmpeg_manager = FFmpegManager()
