"""
Source-pool resolution shared by the FFmpeg manager (failover) and the Xtream
delivery endpoints (balanced load balancing).

A channel's effective source pool is its StreamSource rows ordered by priority.
For back-compat with channels created before the pool existed, a stream with no
source rows falls back to a synthesised pool of [stream_url, backup_url].
"""
import hashlib
from dataclasses import dataclass
from typing import Optional


@dataclass
class SourceRef:
    """A single resolvable source. `id` is None for synthesised fallbacks."""
    url: str
    id: Optional[int] = None
    status: str = "unknown"  # unknown, ok, error
    is_enabled: bool = True
    priority: int = 0


def source_refs(stream, sources) -> list[SourceRef]:
    """Ordered, enabled source pool for a stream.

    `sources` is the stream's StreamSource rows (may be empty). Falls back to the
    legacy stream_url/backup_url when no rows exist so upgrades keep working.
    """
    rows = [s for s in (sources or []) if s.is_enabled and (s.url or "").strip()]
    if rows:
        rows.sort(key=lambda s: (s.priority, s.id))
        return [
            SourceRef(url=s.url.strip(), id=s.id, status=s.status or "unknown",
                      is_enabled=bool(s.is_enabled), priority=s.priority or 0)
            for s in rows
        ]

    synth: list[SourceRef] = []
    for url in (getattr(stream, "stream_url", None), getattr(stream, "backup_url", None)):
        if url and url.strip():
            synth.append(SourceRef(url=url.strip(), id=None, status="unknown"))
    return synth


def source_urls(stream, sources) -> list[str]:
    """Just the ordered URL list — what the FFmpeg failover chain consumes."""
    return [r.url for r in source_refs(stream, sources)]


def pick_source_for_user(refs: list[SourceRef], username: str) -> Optional[SourceRef]:
    """Sticky-by-username choice among healthy mirrors (balanced mode).

    The same username always lands on the same mirror while the healthy set is
    stable, which spreads viewers across origins. Sources marked "error" by the
    health checker are excluded, so a dead mirror's users re-hash onto the
    survivors — i.e. load balancing and failover in one step. If every source is
    unhealthy we fall back to the full enabled pool rather than serving nothing.
    """
    if not refs:
        return None
    healthy = [r for r in refs if r.status != "error"]
    pool = healthy or refs
    pool = sorted(pool, key=lambda r: (r.priority, r.id or 0))
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
    idx = int(digest, 16) % len(pool)
    return pool[idx]
