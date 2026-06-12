"""
WebSocket endpoint for real-time server stats.
Pushes CPU, RAM, bandwidth, active stream count every 2 seconds.
"""
import asyncio
import json
import logging
import psutil
from fastapi import WebSocket, WebSocketDisconnect
from app.ffmpeg_manager import ffmpeg_manager

logger = logging.getLogger(__name__)

# Track previous net counters for bandwidth calculation
_prev_net = None


async def stats_sender(websocket: WebSocket):
    global _prev_net
    await websocket.accept()
    try:
        while True:
            try:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory()
                net = psutil.net_io_counters()

                # Calculate bandwidth delta
                bw_out = 0
                bw_in = 0
                if _prev_net:
                    bw_out = max(0, net.bytes_sent - _prev_net.bytes_sent)
                    bw_in = max(0, net.bytes_recv - _prev_net.bytes_recv)
                _prev_net = net

                active = await ffmpeg_manager.get_all_statuses()
                running = [s for s in active if s["status"] == "running"]

                payload = {
                    "type": "stats",
                    "cpu_percent": round(cpu, 1),
                    "ram_percent": round(ram.percent, 1),
                    "ram_used_mb": round(ram.used / 1024 / 1024),
                    "ram_total_mb": round(ram.total / 1024 / 1024),
                    "bw_out_kbps": round(bw_out * 8 / 1024, 1),
                    "bw_in_kbps": round(bw_in * 8 / 1024, 1),
                    "active_streams": len(running),
                    "streams": [
                        {
                            "id": s["stream_id"],
                            "status": s["status"],
                            "viewers": s["viewer_count"],
                        }
                        for s in active
                    ],
                }
                await websocket.send_text(json.dumps(payload))
            except Exception as e:
                logger.error(f"WS stats error: {e}")
                break
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WS connection error: {e}")
