# IPTV Panel (Kobe Stores)

Personal IPTV panel — full Xtream Codes API, HLS streaming via FFmpeg, React dashboard.

| URL | Purpose | Cloudflare |
|---|---|---|
| `tv.keitanyfrank.store` | Admin dashboard | Proxied (orange cloud) |
| `live.keitanyfrank.store` | Xtream / HLS for players | DNS only (grey cloud) |

**Server:** Linode 8GB / 4 CPU / 160GB — London (`172.237.102.20`)

---

## One-Command Install (Ubuntu 22.04)

SSH into your VPS as root, then run:

```bash
apt update -y && apt upgrade -y && apt install -y git curl wget && \
git clone https://github.com/justkeitany/Kobe_stores.git /tmp/mzeekobe && \
chmod +x /tmp/mzeekobe/install.sh && bash /tmp/mzeekobe/install.sh
```

The installer will:
- Install Python 3.11, Node 20, PostgreSQL, Redis, FFmpeg, Nginx
- Build and deploy the frontend to `/var/www/iptv-panel`
- Set up the FastAPI backend as a systemd service
- Configure Nginx with two virtual hosts (tv + live)
- Tune the kernel for low-latency HLS streaming
- Print your DB password at the end — **save it**

---

## After Install

**Enable real SSL:**
```bash
certbot --nginx -d tv.keitanyfrank.store -d live.keitanyfrank.store \
  --agree-tos -m admin@keitanyfrank.store --non-interactive
```

**Update live (pull from GitHub + redeploy):**
```bash
sudo bash /opt/iptv-panel/scripts/update.sh
```

**Reset forgotten password:**
```bash
sudo bash /opt/iptv-panel/scripts/reset-password.sh
```

---

## Xtream Codes Setup in Players

| Field | Value |
|---|---|
| Server URL | `https://live.keitanyfrank.store` |
| Username | your admin username |
| Password | your admin password |
| M3U URL | `https://live.keitanyfrank.store/get.php?username=X&password=Y&type=m3u_plus` |
| XMLTV URL | `https://live.keitanyfrank.store/xmltv.php?username=X&password=Y` |

---

## Stack

- Frontend: Vite + React + Tailwind CSS (Inter font)
- Backend: Python 3.11 + FastAPI + uvicorn
- Database: PostgreSQL 14+
- Cache: Redis 7
- Streaming: FFmpeg (HLS output, 2s segments)
- Proxy: Nginx (separate vhosts for dashboard vs stream)
