from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://iptv:yourpassword@localhost:5432/iptvpanel"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET: str = "change-this-to-a-long-random-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Admin credentials
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "changeme123"

    # Server
    SERVER_URL: str = "https://live.keitanyfrank.store"
    PANEL_PORT: int = 8000

    # HLS / FFmpeg
    HLS_SEGMENT_TIME: int = 2
    HLS_LIST_SIZE: int = 6
    HLS_OUTPUT_DIR: str = "/var/iptv/hls"
    FFMPEG_PATH: str = "/usr/bin/ffmpeg"

    # Stream health
    MAX_RETRY_ATTEMPTS: int = 5
    HEALTH_CHECK_INTERVAL: int = 30

    # Security
    ADMIN_IP_WHITELIST: str = ""  # comma separated IPs
    RATE_LIMIT_PER_MINUTE: int = 60

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def allowed_admin_ips(self) -> list[str]:
        if not self.ADMIN_IP_WHITELIST:
            return []
        return [ip.strip() for ip in self.ADMIN_IP_WHITELIST.split(",") if ip.strip()]


settings = Settings()
