import asyncio
import subprocess
import logging
from fastapi import APIRouter, Depends
from app.auth import get_current_admin
from app.ffmpeg_manager import ffmpeg_manager
from app.viewers import live_counts

router = APIRouter(prefix="/api/server", tags=["server"])
logger = logging.getLogger(__name__)


@router.get("/stats")
async def get_stats(_=Depends(get_current_admin)):
    """Snapshot of CPU, RAM, disk, active streams."""
    import psutil
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    active_streams = await ffmpeg_manager.get_all_statuses()
    running = [s for s in active_streams if s["status"] == "running"]

    return {
        "cpu_percent": cpu,
        "ram_total_mb": round(ram.total / 1024 / 1024),
        "ram_used_mb": round(ram.used / 1024 / 1024),
        "ram_percent": ram.percent,
        "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
        "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
        "disk_percent": disk.percent,
        "net_bytes_sent": net.bytes_sent,
        "net_bytes_recv": net.bytes_recv,
        "active_stream_count": len(running),
        "streams": active_streams,
    }


@router.get("/connections")
async def get_connections(_=Depends(get_current_admin)):
    """Live concurrent figures (HLS + .ts): active_connections, active_streams."""
    return await live_counts()


@router.get("/processes")
async def get_ffmpeg_processes(_=Depends(get_current_admin)):
    """List all active FFmpeg processes."""
    return await ffmpeg_manager.get_all_statuses()


@router.post("/restart-all-streams")
async def restart_all_streams(_=Depends(get_current_admin)):
    """Kill and restart all running FFmpeg streams."""
    await ffmpeg_manager.stop_all()
    return {"ok": True, "message": "All streams stopped. They will restart on next viewer connection."}


@router.get("/logs")
async def get_logs(lines: int = 100, _=Depends(get_current_admin)):
    """Tail the systemd journal for the iptv-panel service."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", "iptv-panel", "-n", str(lines), "--no-pager", "--output=short"],
            capture_output=True, text=True, timeout=10
        )
        return {"logs": result.stdout or result.stderr}
    except Exception as e:
        return {"logs": f"Could not read logs: {e}"}
