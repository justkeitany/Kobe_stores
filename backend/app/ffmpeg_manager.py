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
import subprocess
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from app.config import settings

logger = logging.getLogger(__name__)

# How long (seconds) a stream may go without a playlist request before it is
# considered idle and stopped. HLS players reload the live playlist roughly
# every segment duration (~2s), so this tolerates a few missed polls while
# still stopping promptly once the viewer leaves.
STREAM_IDLE_TIMEOUT = 8


class StreamProcess:
    def __init__(self, stream_id: int, sources: list[str], stream_name: str):
        self.stream_id = stream_id
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
            "-re",
            "-i", self.current_url,
            # Reconnect options for resilience
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            # Copy streams without re-encoding (lowest latency)
            "-c", "copy",
            # HLS output
            "-f", "hls",
            "-hls_time", str(settings.HLS_SEGMENT_TIME),
            "-hls_list_size", str(settings.HLS_LIST_SIZE),
            "-hls_flags", "delete_segments+append_list+omit_endlist",
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

    async def get_or_create(self, stream_id: int, sources: list[str], stream_name: str) -> StreamProcess:
        async with self._lock:
            sp = self._streams.get(stream_id)
            if sp is None:
                sp = StreamProcess(stream_id, sources, stream_name)
                self._streams[stream_id] = sp
            elif sources and sp.status not in ("running", "starting"):
                # Pick up edited source pools the next time the stream (re)starts.
                sp.sources = [s for s in sources if s] or [""]
                sp.source_index = 0
            return sp

    async def start_stream(
        self, stream_id: int, sources: list[str], stream_name: str, client_key: str = "viewer"
    ) -> StreamProcess:
        self._ensure_reaper()
        sp = await self.get_or_create(stream_id, sources, stream_name)
        sp.heartbeat(client_key)
        if sp.status not in ("running", "starting"):
            await sp.start()
        return sp

    async def stop_stream(self, stream_id: int):
        async with self._lock:
            sp = self._streams.pop(stream_id, None)
        if sp:
            await sp.stop()

    async def restart_stream(self, stream_id: int, sources: Optional[list[str]] = None) -> bool:
        async with self._lock:
            sp = self._streams.get(stream_id)
        if not sp:
            return False
        await sp.stop()
        if sources:
            sp.sources = [s for s in sources if s] or [""]
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
