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


class StreamProcess:
    def __init__(self, stream_id: int, stream_url: str, stream_name: str):
        self.stream_id = stream_id
        self.stream_url = stream_url
        self.stream_name = stream_name
        self.process: Optional[asyncio.subprocess.Process] = None
        self.viewer_count: int = 0
        self.retry_count: int = 0
        self.started_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.status: str = "idle"
        self._lock = asyncio.Lock()
        self._health_task: Optional[asyncio.Task] = None
        self._viewer_dropped = asyncio.Event()

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
            "-i", self.stream_url,
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
        """Monitor process health and restart if it crashes."""
        while True:
            try:
                # Wait max 5s OR until a viewer drops — whichever comes first
                try:
                    await asyncio.wait_for(self._viewer_dropped.wait(), timeout=5)
                    self._viewer_dropped.clear()
                except asyncio.TimeoutError:
                    pass

                if self.viewer_count == 0:
                    logger.info(f"Stream {self.stream_id} has no viewers, stopping")
                    await self.stop()
                    break

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

                    self.status = "starting"
                    await asyncio.sleep(min(self.retry_count * 2, 30))
                    await self.start()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error for stream {self.stream_id}: {e}")
                await asyncio.sleep(5)

    def add_viewer(self):
        self.viewer_count += 1

    def remove_viewer(self):
        self.viewer_count = max(0, self.viewer_count - 1)
        if self.viewer_count == 0:
            # Signal health monitor to wake up immediately
            self._viewer_dropped.set()


class FFmpegManager:
    def __init__(self):
        self._streams: Dict[int, StreamProcess] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, stream_id: int, stream_url: str, stream_name: str) -> StreamProcess:
        async with self._lock:
            if stream_id not in self._streams:
                self._streams[stream_id] = StreamProcess(stream_id, stream_url, stream_name)
            return self._streams[stream_id]

    async def start_stream(self, stream_id: int, stream_url: str, stream_name: str) -> StreamProcess:
        sp = await self.get_or_create(stream_id, stream_url, stream_name)
        sp.add_viewer()
        if sp.status not in ("running", "starting"):
            await sp.start()
        return sp

    async def viewer_left(self, stream_id: int):
        async with self._lock:
            sp = self._streams.get(stream_id)
        if sp:
            sp.remove_viewer()

    async def stop_stream(self, stream_id: int):
        async with self._lock:
            sp = self._streams.pop(stream_id, None)
        if sp:
            await sp.stop()

    async def restart_stream(self, stream_id: int) -> bool:
        async with self._lock:
            sp = self._streams.get(stream_id)
        if not sp:
            return False
        await sp.stop()
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
            "viewer_count": sp.viewer_count,
            "retry_count": sp.retry_count,
            "last_error": sp.last_error,
            "started_at": sp.started_at.isoformat() if sp.started_at else None,
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
