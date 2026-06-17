# IPTV Panel

Self-hosted IPTV panel with **Xtream Codes API**, **HLS streaming (FFmpeg)**, and an **admin dashboard**.
Works with TiviMate, IPTV Smarters, GSE, VLC, Kodi and any Xtream-compatible player.

- ⚡ FastAPI backend · React dashboard · PostgreSQL · Redis · Nginx
- 📺 Xtream Codes API (`player_api.php`, `get.php`, `xmltv.php`, live streams)
- 🔐 JWT auth with forced first-login password change
- 🌐 **No domain required** — install, open `http://YOUR_VPS_IP:8080`, then set your
  domain later from the dashboard. It is embedded in all playlists and Xtream links.

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
Dashboard:   http://203.0.113.10:8080
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

1. Open **`http://YOUR_VPS_IP:8080`** (port `80` also works) and log in with `admin` / `admin`.
2. Set a new username and password when prompted.
3. Add categories and streams, then create users under **Users**.
4. Share the Xtream details with your players — by default they use `http://YOUR_VPS_IP:8080`.

## Using your own domain

You set the domain **inside the dashboard**, not at install time:

1. Point your domain's DNS (an `A` record) at your VPS IP.
2. In the dashboard go to **Settings → Public Server URL** and enter your domain,
   e.g. `http://your.domain.com`. Save.
3. All M3U playlists and Xtream links now use your domain. Because Nginx accepts any
   host, `http://your.domain.com/get.php?...` works immediately over HTTP — no extra config.

### Optional: HTTPS on your domain

```bash
sudo certbot --nginx -d your.domain.com --agree-tos -m you@email.com --non-interactive
```

Then set the Public Server URL to `https://your.domain.com`.

---

## Maintenance

Update to the latest version:

```bash
sudo bash /opt/iptv-panel/scripts/update.sh
```

Reset the admin password:

```bash
sudo bash /opt/iptv-panel/scripts/reset-password.sh
```

---

## Notes

- The backend listens on `127.0.0.1:8000`; Nginx serves the panel + API + Xtream + HLS
  on ports **80** and **8080**.
- Each install is **single-admin** (one operator per VPS).
- An advanced Cloudflare-proxied HTTPS Nginx config is included as
  `nginx/iptv-panel-cloudflare.conf.example`.
