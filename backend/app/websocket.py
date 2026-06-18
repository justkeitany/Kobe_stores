"""
WebSocket endpoint for real-time server stats.
Pushes CPU, RAM, bandwidth, active stream count every 2 seconds.
Single worker only — ffmpeg_manager state is in-process.
"""
import asyncio
import json
import logging
import time
import psutil
from fastapi import WebSocket, WebSocketDisconnect
from app.ffmpeg_manager import ffmpeg_manager

logger = logging.getLogger(__name__)


async def stats_sender(websocket: WebSocket):
    await websocket.accept()

    # Each connection tracks its own previous network counters
    prev_net = psutil.net_io_counters()

    try:
        while True:
            try:
                cpu  = psutil.cpu_percent(interval=None)
                ram  = psutil.virtual_memory()
                net  = psutil.net_io_counters()

                bw_out = max(0, net.bytes_sent - prev_net.bytes_sent)
                bw_in  = max(0, net.bytes_recv - prev_net.bytes_recv)
                prev_net = net

                active  = await ffmpeg_manager.get_all_statuses()
                # A stream counts as "active" only while it has a live viewer,
                # so the dashboard drops within ~8s of the viewer leaving.
                watched = [s for s in active if s["viewer_count"] > 0]

                payload = {
                    "type": "stats",
                    "cpu_percent":  round(cpu, 1),
                    "ram_percent":  round(ram.percent, 1),
                    "ram_used_mb":  round(ram.used  / 1024 / 1024),
                    "ram_total_mb": round(ram.total / 1024 / 1024),
                    "bw_out_kbps":  round(bw_out * 8 / 1024, 1),
                    "bw_in_kbps":   round(bw_in  * 8 / 1024, 1),
                    "uptime_seconds": round(time.time() - psutil.boot_time()),
                    "active_streams": len(watched),
                    "streams": [
                        {
                            "id":      s["stream_id"],
                            "status":  s["status"],
                            "viewers": s["viewer_count"],
                        }
                        for s in active
                    ],
                }
                await websocket.send_text(json.dumps(payload))

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"WS stats error: {e}")
                break

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
