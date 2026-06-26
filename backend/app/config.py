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
    # Leave empty to auto-derive the public URL from each request (IP:port).
    # The dashboard's "Public Server URL" setting (stored in the DB) overrides this.
    SERVER_URL: str = ""
    PANEL_PORT: int = 8000

    # Cloudflare R2 (S3-compatible) — premium-playlist backup/export. All blank by
    # default; export stays dormant until these are set. Create an R2 API token
    # with Object Read & Write scoped to the bucket.
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    R2_PREFIX: str = "playlists/"
    # Optional public base (custom domain) for friendly return URLs only.
    R2_PUBLIC_BASE: str = ""

    # HLS / FFmpeg
    HLS_SEGMENT_TIME: int = 2
    HLS_LIST_SIZE: int = 6
    HLS_OUTPUT_DIR: str = "/var/iptv/hls"
    FFMPEG_PATH: str = "/usr/bin/ffmpeg"

    # yt-dlp — resolves fresh HLS manifests for YouTube live streams
    YTDLP_PATH: str = "/usr/local/bin/yt-dlp"
    # YouTube blocks/ratelimits datacenter IPs with "Sign in to confirm you're
    # not a bot" / HTTP 429. Set one or both of these in .env to get past it:
    #   YTDLP_COOKIES = /opt/iptv-panel/backend/cookies.txt  (Netscape cookie file)
    #   YTDLP_PROXY   = http://user:pass@host:port           (residential proxy)
    YTDLP_COOKIES: str = ""
    YTDLP_PROXY: str = ""

    # Stream health
    MAX_RETRY_ATTEMPTS: int = 5
    HEALTH_CHECK_INTERVAL: int = 30

    # Security
    ADMIN_IP_WHITELIST: str = ""  # comma separated IPs
    RATE_LIMIT_PER_MINUTE: int = 60

    # AI assistant (Claude). Keys live in .env, never in the repo. Empty = AI off.
    ANTHROPIC_API_KEY: str = ""
    AI_MODEL: str = "claude-opus-4-8"
    # suggest | autofix  — autofix applies only whitelisted reversible actions.
    # Background monitoring auto-fixes by default.
    AI_AUTONOMY: str = "autofix"
    # Background monitor sweep interval (seconds).
    AI_MONITOR_INTERVAL: int = 1800
    # Channels probed per sweep cycle (rotates through all over a few cycles).
    AI_HEALTH_BATCH: int = 400
    # Hard ceiling on Claude calls per day (cost guard).
    AI_DAILY_CALL_CAP: int = 200
    # Multiple providers for failover (when one gateway is down, try the next).
    # JSON list, e.g.:
    #   [{"name":"aerolink","type":"cli","base_url":"https://capi.aerolink.lat/","api_key":"aero_..."},
    #    {"name":"anthropic","type":"sdk","api_key":"sk-ant-..."}]
    # type "sdk" = raw Anthropic API; "cli" = routed through the claude CLI
    # (for gateways that only accept the Claude Code client). ANTHROPIC_API_KEY,
    # if set, is appended as a final "sdk" provider.
    AI_PROVIDERS: str = ""
    # Path to the claude CLI binary and a writable HOME for it (cli transport).
    CLAUDE_BIN: str = "claude"
    AI_CLI_HOME: str = "/opt/iptv-panel/.aihome"

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def allowed_admin_ips(self) -> list[str]:
        if not self.ADMIN_IP_WHITELIST:
            return []
        return [ip.strip() for ip in self.ADMIN_IP_WHITELIST.split(",") if ip.strip()]


settings = Settings()
