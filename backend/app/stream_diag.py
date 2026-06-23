"""Stream diagnostics — measure every step of the playback pipeline.

Usage: python stream_diag.py STREAM_ID  (for imported streams)
       python stream_diag.py --url URL   (for any URL)

Measures: DNS, TCP connect, TLS handshake, first byte, ffprobe,
FFmpeg startup, first segment, and ongoing segment health.
"""
import asyncio, sys, json, time, os, subprocess, hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"


@dataclass
class DiagResult:
    url: str
    name: str = ""
    stream_id: int = 0
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    probe: dict = field(default_factory=dict)
    segments: list[dict] = field(default_factory=list)

    def add_error(self, e: str):
        self.errors.append(e)

    def to_dict(self):
        return {
            "url": self.url, "name": self.name, "passed": self.passed,
            "errors": self.errors, "timings": self.timings,
            "probe": self.probe, "segments": self.segments,
        }


async def measure_connectivity(url: str, result: DiagResult):
    """Measure DNS, TCP, TLS, and first-byte timing via httpx."""
    import socket
    from urllib.parse import urlparse
    p = urlparse(url)

    # DNS
    t0 = time.monotonic()
    try:
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
        result.timings["dns_ms"] = round((time.monotonic() - t0) * 1000, 1)
    except Exception as e:
        result.timings["dns_failed"] = str(e)
        result.add_error(f"DNS: {e}")
        return False

    # TCP + TLS + first byte
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                      headers={"User-Agent": UA}) as client:
            resp = await client.get(url)
            elapsed = (time.monotonic() - t0) * 1000
            result.timings["first_byte_ms"] = round(elapsed, 1)
            result.timings["http_status"] = resp.status_code
            result.timings["content_type"] = resp.headers.get("content-type", "?")
            body = resp.content
            result.timings["body_bytes"] = len(body)

            if b"#EXTM3U" in body[:500]:
                result.timings["is_hls"] = True
                # Count segments/extinf
                text = body[:20000].decode(errors="replace")
                extinf_count = text.count("#EXTINF")
                result.timings["extinf_lines"] = extinf_count
            else:
                result.timings["is_hls"] = False
                first = body[:200].decode(errors="replace")
                result.add_error(f"Not an M3U8 playlist. First bytes: {first[:100]}")

            if resp.status_code >= 400:
                result.add_error(f"HTTP {resp.status_code}")
            return resp.status_code < 400
    except httpx.TimeoutException:
        elapsed = (time.monotonic() - t0) * 1000
        result.timings["first_byte_ms"] = round(elapsed, 1)
        result.add_error(f"TIMEOUT after {elapsed:.0f}ms")
        return False
    except Exception as e:
        result.add_error(f"Connection: {e}")
        return False


async def run_ffprobe(url: str, result: DiagResult):
    """Probe stream metadata: codecs, bitrate, frame rate."""
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            FFPROBE, "-v", "error", "-show_entries",
            "stream=codec_name,codec_type,bit_rate,r_frame_rate,width,height",
            "-of", "json", "-analyzeduration", "3000000",
            "-user_agent", UA, url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        elapsed = (time.monotonic() - t0) * 1000
        result.timings["ffprobe_ms"] = round(elapsed, 1)

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[:200]
            result.add_error(f"ffprobe failed (rc={proc.returncode}): {err}")
            return

        data = json.loads(stdout)
        streams = data.get("streams", [])
        result.probe["stream_count"] = len(streams)
        for s in streams:
            if s.get("codec_type") == "video":
                result.probe["video_codec"] = s.get("codec_name", "?")
                result.probe["bitrate"] = s.get("bit_rate", "?")
                result.probe["resolution"] = f"{s.get('width','?')}x{s.get('height','?')}"
            elif s.get("codec_type") == "audio":
                result.probe["audio_codec"] = s.get("codec_name", "?")

        result.timings["has_video"] = any(s.get("codec_type") == "video" for s in streams)
    except asyncio.TimeoutError:
        result.add_error("ffprobe TIMEOUT (15s)")
    except Exception as e:
        result.add_error(f"ffprobe: {e}")


async def test_ffmpeg_start(url: str, result: DiagResult):
    """Start FFmpeg and measure time to first HLS segment."""
    hls_dir = f"/tmp/diag_hls_{os.getpid()}"
    os.makedirs(hls_dir, exist_ok=True)
    playlist = os.path.join(hls_dir, "index.m3u8")

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt+nobuffer",
            "-analyzeduration", "1000000", "-probesize", "1000000",
            "-user_agent", UA,
            "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_on_http_error", "4xx,5xx",
            "-reconnect_delay_max", "5",
            "-rw_timeout", "15000000",
            "-i", url,
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments",
            "-hls_segment_filename", os.path.join(hls_dir, "seg%d.ts"),
            playlist,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for first segment to appear (max 60s)
        seg_pattern = os.path.join(hls_dir, "seg0.ts")
        for attempt in range(120):
            await asyncio.sleep(0.5)
            if os.path.exists(seg_pattern):
                elapsed = (time.monotonic() - t0) * 1000
                result.timings["first_segment_ms"] = round(elapsed, 1)
                break

        if not os.path.exists(seg_pattern):
            result.add_error("FFmpeg: no segment produced within 60s")
            stderr = b""
            try:
                proc.kill()
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=2)
            except Exception:
                pass
            if stderr:
                err = stderr.decode(errors="replace")[:300]
                result.add_error(f"FFmpeg stderr: {err}")
            return

        # Measure segment health for 15 seconds
        segments_seen = set()
        observed_sizes = []
        t_seg_start = time.monotonic()
        while time.monotonic() - t_seg_start < 15:
            await asyncio.sleep(1)
            for f in os.listdir(hls_dir):
                if f.startswith("seg") and f.endswith(".ts") and f not in segments_seen:
                    segments_seen.add(f)
                    fpath = os.path.join(hls_dir, f)
                    try:
                        size = os.path.getsize(fpath)
                        observed_sizes.append(size)
                    except OSError:
                        pass

        result.timings["segment_count"] = len(segments_seen)
        if observed_sizes:
            result.timings["avg_segment_bytes"] = sum(observed_sizes) // len(observed_sizes)
            result.timings["min_segment_bytes"] = min(observed_sizes)
            result.timings["max_segment_bytes"] = max(observed_sizes)

        # Check for FFmpeg errors
        if proc.returncode is not None:
            result.add_error(f"FFmpeg exited with rc={proc.returncode}")
        try:
            stderr_data = b""
            try:
                stderr_data = await asyncio.wait_for(proc.stderr.read(4096), timeout=2)
            except asyncio.TimeoutError:
                pass
            if stderr_data:
                err = stderr_data.decode(errors="replace")[:300]
                if "error" in err.lower() or "fail" in err.lower() or "cannot" in err.lower():
                    result.add_error(f"FFmpeg: {err}")
        except Exception:
            pass

        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            pass

    except Exception as e:
        result.add_error(f"FFmpeg startup: {e}")
    finally:
        # Cleanup
        import shutil
        shutil.rmtree(hls_dir, ignore_errors=True)


async def diagnose(url: str, name: str = "", stream_id: int = 0) -> DiagResult:
    result = DiagResult(url=url, name=name, stream_id=stream_id)
    t0 = time.monotonic()

    print(f"\n{'='*60}")
    print(f"DIAGNOSING: {name or 'URL'}")
    print(f"URL: {url[:120]}")
    print(f"{'='*60}")

    # Step 1: Connectivity
    print("\n[1/4] Testing connectivity...")
    ok = await measure_connectivity(url, result)
    print(f"  DNS: {result.timings.get('dns_ms','FAIL')}ms")
    print(f"  First byte: {result.timings.get('first_byte_ms','FAIL')}ms")
    print(f"  HTTP: {result.timings.get('http_status','?')}")
    print(f"  HLS playlist: {result.timings.get('is_hls', False)}")
    print(f"  EXTINF lines: {result.timings.get('extinf_lines', 0)}")

    if not ok:
        result.passed = False
        result.timings["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
        print(f"\n  FAILED: {'; '.join(result.errors)}")
        return result

    # Step 2: ffprobe
    print("\n[2/4] Running ffprobe...")
    await run_ffprobe(url, result)
    p = result.probe
    print(f"  Codec: {p.get('video_codec','?')} @ {p.get('resolution','?')}")
    print(f"  Bitrate: {p.get('bitrate','?')}")
    print(f"  Time: {result.timings.get('ffprobe_ms','?')}ms")

    # Step 3: FFmpeg startup + segment delivery
    print("\n[3/4] Testing FFmpeg restream (passthrough)...")
    await test_ffmpeg_start(url, result)
    print(f"  First segment: {result.timings.get('first_segment_ms','TIMEOUT')}ms")
    print(f"  Segments in 15s: {result.timings.get('segment_count', 0)}")
    print(f"  Avg segment size: {result.timings.get('avg_segment_bytes', 0)} bytes")

    # Step 4: Summary
    result.timings["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
    result.passed = (
        result.timings.get("first_segment_ms", 99999) < 30000
        and result.timings.get("segment_count", 0) >= 2
        and len(result.errors) == 0
    )

    if result.passed:
        print(f"\n[4/4] PASS — stream is healthy")
    else:
        print(f"\n[4/4] FAIL — {len(result.errors)} error(s):")
        for e in result.errors:
            print(f"  ! {e}")

    print(f"\nTotal time: {result.timings['total_ms']}ms")
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print("Usage: python stream_diag.py STREAM_ID")
        print("       python stream_diag.py --url URL [--name NAME]")
        sys.exit(1)

    if sys.argv[1] == "--url":
        url = sys.argv[2]
        name = ""
        for i, a in enumerate(sys.argv):
            if a == "--name" and i + 1 < len(sys.argv):
                name = sys.argv[i + 1]
        result = await diagnose(url, name)
    else:
        sid = int(sys.argv[1])
        from app.database import AsyncSessionLocal
        from app.models import Stream
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            s = await db.execute(select(Stream).where(Stream.id == sid))
            stream = s.scalar_one_or_none()
            if not stream:
                print(f"Stream {sid} not found")
                sys.exit(1)
            result = await diagnose(stream.stream_url, stream.name, stream.id)

    # Save report
    report = json.dumps(result.to_dict(), indent=2, default=str)
    filename = f"/tmp/diag_{int(time.time())}.json"
    with open(filename, "w") as f:
        f.write(report)
    print(f"\nReport saved: {filename}")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
