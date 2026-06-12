# IPTV Panel

Personal IPTV panel with full Xtream Codes API, HLS streaming via FFmpeg, and a TailAdmin-style dashboard.

## Stack

| Layer | Tech |
|---|---|
| Frontend | Vite + React + Tailwind (TailAdmin style) |
| Backend | Python FastAPI |
| Database | PostgreSQL |
| Cache/Rate-limit | Redis |
| Streaming | FFmpeg (HLS output) |
| Proxy | Nginx |
| Domain | live.keitanyfrank.store (Cloudflare) |

## Quick Deploy (Ubuntu 22.04 VPS)

```bash
# Clone or upload the project to your VPS
scp -r iptv-panel/ root@YOUR_VPS:/tmp/

# SSH in and run the installer
ssh root@YOUR_VPS
cd /tmp/iptv-panel
sudo bash scripts/install.sh
```

The installer will:
1. Install all dependencies (Node 20, Python, PostgreSQL, Redis, FFmpeg, Nginx)
2. Set up the database and Redis
3. Build the frontend and deploy it
4. Create a systemd service (`iptv-panel`)
5. Configure Nginx with Cloudflare-compatible settings
6. Tune the kernel for low-latency streaming (BBR TCP, socket buffers)
7. Print your credentials

## Xtream Codes API Endpoints

All endpoints are served by FastAPI — no PHP anywhere.

| Endpoint | Purpose |
|---|---|
| `/player_api.php` | Authentication + channel list |
| `/get.php` | M3U playlist download |
| `/live/{user}/{pass}/{id}.m3u8` | Stream delivery (HLS) |
| `/xmltv.php` | EPG guide data |

**Use in your player:**
- Server: `https://live.keitanyfrank.store`
- Username: `admin`
- Password: *(set during install)*

## Features

- Upload M3U files — auto-imports channels + creates categories from `group-title`
- Create channels manually with name, URL, logo
- Full category management (CRUD, reorder, move streams)
- Bouquets (package categories together)
- EPG via XMLTV sources — auto-fetch + map to channels
- Live server stats (CPU, RAM, bandwidth) via WebSocket
- FFmpeg: starts on first viewer, stops when idle, auto-restarts on crash
- JWT authentication with refresh tokens
- Brute force protection
- Rate limiting via Redis

## Update

```bash
cd /tmp/iptv-panel
sudo bash scripts/update.sh
```

## SSL

The installer creates a self-signed cert. Since you're using Cloudflare:
- Set Cloudflare SSL mode to **Full** or **Flexible**
- Cloudflare handles the public SSL — your server just needs to accept connections

For a proper cert (optional):
```bash
certbot --nginx -d live.keitanyfrank.store --agree-tos -m your@email.com
```

## Ports

| Port | Use |
|---|---|
| 80 | HTTP (Cloudflare proxied) |
| 443 | HTTPS |
| 8000 | FastAPI (internal only, bound to 127.0.0.1) |

## Directory Structure

```
iptv-panel/
├── frontend/          Vite + React dashboard
├── backend/
│   └── app/
│       ├── main.py        FastAPI app entry
│       ├── models.py      SQLAlchemy models
│       ├── ffmpeg_manager.py  Stream process manager
│       └── routers/       API route handlers
├── nginx/             Nginx config
└── scripts/           install.sh + update.sh
```
