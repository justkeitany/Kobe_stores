# IPTV Panel

Self-hosted IPTV panel with **Xtream Codes API**, **HLS streaming (FFmpeg)**, and an **admin dashboard**.
Works with TiviMate, IPTV Smarters, GSE, VLC, Kodi and any Xtream-compatible player.

- ⚡ FastAPI backend · React dashboard · PostgreSQL · Redis · Nginx
- 📺 Xtream Codes API (`player_api.php`, `get.php`, `xmltv.php`, live streams)
- 🔁 **Failover** — give a channel multiple source URLs; FFmpeg fails over to the
  next one automatically when a source dies
- ⚖️ **Load balancing** — in *balanced* mode, viewers are spread across a channel's
  equivalent source mirrors (sticky per user, skips unhealthy mirrors)
- 🎚️ Per-user **bouquets** (channel packages) and **max-connection** limits enforced
- 🔐 JWT auth with forced first-login password change
- 🌐 **No domain required** — install, open `http://YOUR_VPS_IP:25461`, then optionally
  add your own domain from the dashboard with **automatic HTTPS**. The chosen address is
  embedded in all playlists and Xtream links.

---

## Quick install (one command)

On a fresh **Ubuntu 22.04** VPS, run as root:

```bash
curl -fsSL https://raw.githubusercontent.com/justkeitany/Kobe_stores/main/scripts/bootstrap.sh | sudo bash
```

This installs everything (Python, Node, PostgreSQL, Redis, Nginx, FFmpeg), clones the
repo, builds the dashboard, and starts the service. **No domain is asked for.**

When it finishes it prints your dashboard URL and login, e.g.:

```
Dashboard:   http://203.0.113.10:25461
Username:    admin
Password:    admin   (you must change it on first login)
```

### Manual install (alternative)

Run these as root (or with `sudo`):

```bash
apt update -y && apt upgrade -y
apt install -y git curl wget
git clone https://github.com/justkeitany/Kobe_stores.git /tmp/mzeekobe
chmod +x /tmp/mzeekobe/install.sh
bash /tmp/mzeekobe/install.sh
```

---

## First steps after install

1. Open **`http://YOUR_VPS_IP:25461`** (port `80` also works) and log in with `admin` / `admin`.
2. Set a new username and password when prompted.
3. Add categories and streams, then create users under **Users**.
4. Share the Xtream details with your players — by default they use `http://YOUR_VPS_IP:25461`.

## Choose: server IP or your own domain

Open **Settings → Access & Domain** and pick one. You can switch at any time.

**Option 1 — Use server IP (default):** playlists and Xtream links use
`http://YOUR_VPS_IP:25461`. Nothing else to do.

**Option 2 — Use my domain (with automatic HTTPS):**

1. Point your domain's DNS (an `A` record) at your VPS IP.
2. In **Settings → Access & Domain**, choose **Use my domain**, enter it
   (e.g. `tv.example.com`) and click **Save & enable HTTPS**.
3. The site works over `http://your.domain` immediately, and a Let's Encrypt
   certificate is obtained **automatically in the background**. When it finishes,
   the status turns green and all playlists/Xtream links switch to
   `https://your.domain`. Renewal is automatic.

> If HTTPS shows "failed", it almost always means the domain's DNS isn't pointing at
> the server yet — fix the `A` record and click save again. The panel keeps working
> over HTTP in the meantime.

---

## Failover & load balancing

Each channel has an ordered **pool of source URLs** (edit a stream → *Source URLs*).
The first entry is the primary; the rest are backups. Pick a **delivery mode**:

- **Restream (default):** one FFmpeg process pulls the channel and serves HLS to
  every viewer. If the active source crashes, it **fails over** to the next URL in
  the pool automatically (and keeps retrying down the list).
- **Balanced:** players are handed a source URL **directly**, chosen consistently
  per username across the pool's mirrors. This **spreads viewers over equivalent
  origin servers** so no single source is hammered. A background health check
  marks dead mirrors so users are steered onto healthy ones (failover + balancing
  in one). Use this when you have several equivalent origins for the same channel.

> Balanced mode bypasses restreaming, so the panel's `max_connections` limit and
> viewer counts don't apply to those channels — the load is deliberately offloaded
> to the origin mirrors.

## Maintenance

Update to the latest version:

```bash
sudo bash /opt/iptv-panel/scripts/update.sh
```

Reset the admin password:

```bash
sudo bash /opt/iptv-panel/scripts/reset-password.sh
```

### Clean reinstall (wipe and install the latest)

Removes the service, files, nginx config and database, then reinstall fresh:

```bash
# 1. Clean the VPS
curl -fsSL https://raw.githubusercontent.com/justkeitany/Kobe_stores/main/scripts/uninstall.sh | sudo bash

# 2. Install the latest
curl -fsSL https://raw.githubusercontent.com/justkeitany/Kobe_stores/main/scripts/bootstrap.sh | sudo bash
```

Add `KEEP_DB=1` before the uninstall command to keep your streams/users/settings.

---

## Notes

- The backend listens on `127.0.0.1:8000`; Nginx serves the panel + API + Xtream + HLS
  on ports **80**, **25461** and **8080** (and **443** once a domain's HTTPS is enabled).
  All three are catch-all, so players work on any of them; the dashboard advertises the
  dedicated Xtream player port **8080** in IP mode, while **25461** is the panel port.
- Each install is **single-admin** (one operator per VPS).
- An advanced Cloudflare-proxied HTTPS Nginx config is included as
  `nginx/iptv-panel-cloudflare.conf.example`.
