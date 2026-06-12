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

    # Stream status
    status = Column(String(50), default="idle")  # idle, running, error, stopped
    last_error = Column(Text, nullable=True)
    last_checked = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    category = relationship("StreamCategory", back_populates="streams")
    connections = relationship("Connection", back_populates="stream")


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


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
