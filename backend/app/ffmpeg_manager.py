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
    "-fflags", "+genpts+discardcorrupt",
    "-analyzeduration", "2000000",
    "-probesize", "2000000",
    "-thread_queue_size", "4096",
    "-re",
]


# How long (seconds) a stream may go without a playlist request before it is
# considered idle and stopped. It must comfortably exceed a player's polling
# gap: adaptive (multi-variant) players split polls between the master and the
# current variant and buffer well ahead, so short windows (e.g. 8s) falsely
# reap an actively-watched channel and cause a restart/buffering death spiral.
STREAM_IDLE_TIMEOUT = 45


# Transcode ladder. "auto" copies the source codec untouched (no CPU, full
# bandwidth). The others scale down to a height cap and bound the bitrate so weak
# connections buffer less. scale=-2:H keeps the aspect ratio and an even width.
QUALITY_PROFILES: Dict[str, dict] = {
    "low":    {"height": 480,  "v_bitrate": "1000k", "maxrate": "1200k", "bufsize": "2400k", "a_bitrate": "96k"},
    "medium": {"height": 720,  "v_bitrate": "2500k", "maxrate": "3000k", "bufsize": "6000k", "a_bitrate": "128k"},
    "high":   {"height": 1080, "v_bitrate": "4500k", "maxrate": "5400k", "bufsize": "10800k", "a_bitrate": "160k"},
}
VALID_QUALITIES = {"auto", *QUALITY_PROFILES}


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
    def __init__(self, stream_id: int, sources: list[str], stream_name: str, quality: str = "auto"):
        self.stream_id = stream_id
        self.quality = quality if quality in VALID_QUALITIES else "auto"
        # Adaptive HLS (one shared multi-variant ladder, player-driven). Decided
        # at first start(): only when quality is "auto" and the governor allows.
        self.multivariant: bool = False
        self._holds_mv: bool = False
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
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", resolve_pluto_url(self.current_url),
            "-filter_complex",
            "[0:v]split=2[a][b];[a]scale=-2:720[v720o];[b]scale=-2:480[v480o]",
            # v0 — source passthrough (no transcode)
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v:0", "copy", "-c:a:0", "copy",
            # v1 — 720p, capped to fit ~2 Mbps lines
            "-map", "[v720o]", "-map", "0:a:0?",
            "-c:v:1", "libx264", "-preset", "veryfast", "-threads", str(ABR_TRANSCODE_THREADS),
            "-b:v:1", "1250k", "-maxrate:v:1", "1450k", "-bufsize:v:1", "3000k",
            "-c:a:1", "aac", "-b:a:1", "128k",
            # v2 — 480p
            "-map", "[v480o]", "-map", "0:a:0?",
            "-c:v:2", "libx264", "-preset", "veryfast", "-threads", str(ABR_TRANSCODE_THREADS),
            "-b:v:2", "600k", "-maxrate:v:2", "750k", "-bufsize:v:2", "1500k",
            "-c:a:2", "aac", "-b:a:2", "96k",
            "-flush_packets", "1",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "10",
            "-hls_flags", "delete_segments+append_list+omit_endlist",
            "-hls_segment_type", "mpegts",
            "-master_pl_name", "master.m3u8",
            "-hls_segment_filename", os.path.join(self.hls_dir, "v%v", "seg%d.ts"),
            "-var_stream_map", "v:0,a:0 v:1,a:1 v:2,a:2",
            os.path.join(self.hls_dir, "v%v", "index.m3u8"),
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
            # Input-side buffering (probe/analyze window, thread queue, -re).
            *FFMPEG_INPUT_BUFFER_ARGS,
            # Pluto channel URLs are rewritten to the jmp2.uk resolver, which
            # redirects to a working stream. Non-Pluto URLs pass through.
            "-i", resolve_pluto_url(self.current_url),
            # Reconnect options for resilience
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            # Stream copy ('auto') or transcode down to the selected quality tier.
            *_codec_args(self.quality),
            # HLS output — flush each packet and hold a deeper segment window so
            # players have more buffered ahead of the live edge.
            "-flush_packets", "1",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "10",
            "-hls_flags", "delete_segments+append_list+split_by_time",
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
                # Adaptive HLS when quality is auto and the governor has room;
                # otherwise a single stream (passthrough for auto, transcode for
                # an explicit tier). Decided once, kept across crash-restarts.
                if self.quality == "auto" and not self._holds_mv:
                    transcode_governor.start()
                    if await transcode_governor.try_acquire_mv():
                        self.multivariant = True
                        self._holds_mv = True
                self._reset_hls_dir()
                cmd = (
                    self._build_multivariant_cmd()
                    if self.multivariant
                    else self._build_ffmpeg_cmd()
                )
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
                    if self.process.stderr:
                        try:
                            stderr = await asyncio.wait_for(
                                self.process.stderr.read(2048), timeout=1
                            )
                            self.last_error = stderr.decode(errors="replace")
                        except asyncio.TimeoutError:
                            pass

                    logger.warning(
                        f"Stream {self.stream_id} crashed (rc={returncode}), "
                        f"retry {self.retry_count}/{settings.MAX_RETRY_ATTEMPTS}"
                    )
                    self.retry_count += 1

                    if self.retry_count > settings.MAX_RETRY_ATTEMPTS:
                        self.status = "error"
                        logger.error(f"Stream {self.stream_id} exceeded max retries, giving up")
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
        self, stream_id: int, sources: list[str], stream_name: str, quality: str = "auto"
    ) -> StreamProcess:
        async with self._lock:
            sp = self._streams.get(stream_id)
            if sp is None:
                sp = StreamProcess(stream_id, sources, stream_name, quality)
                self._streams[stream_id] = sp
            elif sp.status not in ("running", "starting"):
                # Pick up edited source pools / quality the next time it (re)starts.
                if sources:
                    sp.sources = [s for s in sources if s] or [""]
                    sp.source_index = 0
                sp.quality = quality if quality in VALID_QUALITIES else "auto"
            return sp

    async def start_stream(
        self, stream_id: int, sources: list[str], stream_name: str,
        client_key: str = "viewer", quality: str = "auto",
    ) -> StreamProcess:
        self._ensure_reaper()
        sp = await self.get_or_create(stream_id, sources, stream_name, quality)
        sp.heartbeat(client_key)
        if sp.status not in ("running", "starting"):
            await sp.start()
        return sp

    async def stop_stream(self, stream_id: int):
        async with self._lock:
            sp = self._streams.pop(stream_id, None)
        if sp:
            await sp.stop()

    async def restart_stream(
        self, stream_id: int, sources: Optional[list[str]] = None, quality: Optional[str] = None
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
