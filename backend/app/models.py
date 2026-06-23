from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, BigInteger, Float, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class StreamCategory(Base):
    __tablename__ = "stream_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    icon = Column(String(500), nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    streams = relationship("Stream", back_populates="category")


class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(500), nullable=False)
    stream_url = Column(Text, nullable=False)
    backup_url = Column(Text, nullable=True)
    logo_url = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("stream_categories.id"), nullable=True)
    is_enabled = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    epg_channel_id = Column(String(255), nullable=True)

    # How viewers are served:
    #   "restream" — FFmpeg pulls one source and fans out HLS; the source pool
    #                acts as an ordered failover chain.
    #   "balanced" — players are handed a source URL directly, picked sticky by
    #                username across healthy mirrors (load balancing + failover).
    delivery_mode = Column(String(20), default="restream", nullable=False)

    # Output quality. "auto" copies the source codec untouched (lowest CPU,
    # highest bandwidth); the others transcode down to cap resolution/bitrate so
    # weak connections buffer less. Pluto channels default to "low".
    #   auto | low (480p) | medium (720p) | high (1080p)
    quality = Column(String(10), default="auto", nullable=False)

    # Optional ISO country code (e.g. "GB", "US"). When set, the initial M3U8
    # playlist is resolved THROUGH a residential proxy in that country to bypass
    # geo-blocks; heavy segment traffic still flows direct (app/proxy_resolver.py).
    # Null = no proxy (default behaviour).
    proxy_country = Column(String(8), nullable=True)

    # Stream status
    status = Column(String(50), default="idle")  # idle, running, error, stopped
    last_error = Column(Text, nullable=True)
    last_checked = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    category = relationship("StreamCategory", back_populates="streams")
    connections = relationship("Connection", back_populates="stream")
    sources = relationship(
        "StreamSource",
        back_populates="stream",
        cascade="all, delete-orphan",
        order_by="StreamSource.priority, StreamSource.id",
    )


class StreamSource(Base):
    """An ordered pool of equivalent source URLs for one channel.

    Used as a failover chain in restream mode and as the mirror set that viewers
    are load-balanced across in balanced mode. `priority` is ascending (0 first).
    """
    __tablename__ = "stream_sources"

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(Integer, ForeignKey("streams.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(Text, nullable=False)
    priority = Column(Integer, default=0)
    is_enabled = Column(Boolean, default=True)
    status = Column(String(20), default="unknown")  # unknown, ok, error
    last_checked = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    stream = relationship("Stream", back_populates="sources")


class Bouquet(Base):
    __tablename__ = "bouquets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    bouquet_categories = relationship("BouquetCategory", back_populates="bouquet")


class BouquetCategory(Base):
    __tablename__ = "bouquet_categories"

    id = Column(Integer, primary_key=True, index=True)
    bouquet_id = Column(Integer, ForeignKey("bouquets.id", ondelete="CASCADE"))
    category_id = Column(Integer, ForeignKey("stream_categories.id", ondelete="CASCADE"))
    sort_order = Column(Integer, default=0)

    bouquet = relationship("Bouquet", back_populates="bouquet_categories")
    category = relationship("StreamCategory")


class Connection(Base):
    __tablename__ = "connections"

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(Integer, ForeignKey("streams.id", ondelete="SET NULL"), nullable=True)
    ip_address = Column(String(45), nullable=False)
    user_agent = Column(Text, nullable=True)
    device_id = Column(String(255), nullable=True)
    connected_at = Column(DateTime(timezone=True), server_default=func.now())
    disconnected_at = Column(DateTime(timezone=True), nullable=True)
    bytes_sent = Column(BigInteger, default=0)

    stream = relationship("Stream", back_populates="connections")


class ConnectionLog(Base):
    __tablename__ = "connection_logs"

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(Integer, nullable=True)
    stream_name = Column(String(500), nullable=True)
    ip_address = Column(String(45), nullable=False)
    user_agent = Column(Text, nullable=True)
    duration_seconds = Column(Integer, default=0)
    bytes_sent = Column(BigInteger, default=0)
    connected_at = Column(DateTime(timezone=True))
    disconnected_at = Column(DateTime(timezone=True))


class EpgSource(Base):
    __tablename__ = "epg_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    is_enabled = Column(Boolean, default=True)
    last_updated = Column(DateTime(timezone=True), nullable=True)
    update_interval_hours = Column(Integer, default=24)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EpgData(Base):
    __tablename__ = "epg_data"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(String(255), nullable=False, index=True)
    channel_name = Column(String(500), nullable=True)
    title = Column(String(1000), nullable=False)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    category = Column(String(255), nullable=True)
    source_id = Column(Integer, ForeignKey("epg_sources.id", ondelete="CASCADE"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(255), nullable=False)
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(Integer, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Playlist(Base):
    """A saved external M3U playlist URL (e.g. an M3USe shared link).

    Cards on the Playlists page are rendered from cached metadata
    (``channel_count`` + a handful of sample ``logos``) so the list loads
    without re-fetching every multi-MB feed. The full channel list is parsed
    live on demand when the user opens a playlist to import from it.
    """
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    channel_count = Column(Integer, default=0)
    logos = Column(JSON, nullable=True)  # sample logo URLs for the avatar stack
    # Cached parsed channel list [{name,logo,url,category}] so the Channels page
    # can show every playlist channel without re-fetching feeds. Filled by the
    # health sweep / refresh (which already fetch + parse — no extra requests).
    channels = Column(JSON, nullable=True)
    # Short channel-health summary from the last refresh, e.g. "27/30 live".
    health = Column(String(255), nullable=True)
    last_refreshed = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ChannelHealth(Base):
    """Last probed health of a channel (keyed by its source URL), for imported
    streams and playlist channels alike. Filled by the background sweep so the
    Channels page shows real online/offline/geo instead of an optimistic guess."""
    __tablename__ = "channel_health"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(1000), index=True, nullable=False)
    status = Column(String(16), nullable=False)  # online | offline | geo
    last_checked = Column(DateTime(timezone=True), nullable=True)


class AiEvent(Base):
    """An action or output produced by the Claude assistant — diagnoses,
    auto-applied fixes, daily digests, and chat answers. The panel's AI audit
    trail (autonomous fixes are also mirrored into AuditLog)."""
    __tablename__ = "ai_events"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(20), nullable=False, index=True)  # diagnosis|action|digest|chat
    stream_id = Column(Integer, nullable=True, index=True)
    title = Column(String(255), nullable=False)
    detail = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
