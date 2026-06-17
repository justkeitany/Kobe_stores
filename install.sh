#!/bin/bash
# ================================================================
#  IPTV Panel — One-command installer for Ubuntu 22.04
#  Run as root on a fresh Ubuntu 22.04 VPS
# ================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗] $1${NC}"; exit 1; }
step()  { echo -e "\n${CYAN}${BOLD}══ $1 ══${NC}"; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash install.sh"

# ── Config ───────────────────────────────────────────────────────
APP_DIR="/opt/iptv-panel"
HLS_DIR="/var/iptv/hls"
WEB_DIR="/var/www/iptv-panel"
REPO_DIR="/tmp/mzeekobe"

PANEL_PORT_HTTP=25461   # dashboard / Xtream port advertised to users (Xtream Codes default)

DB_NAME="iptvpanel"
DB_USER="iptv"
DB_PASS=$(openssl rand -hex 20)
JWT_SECRET=$(openssl rand -hex 32)

step "System update"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

step "Enabling Python 3.11 (deadsnakes PPA)"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget git unzip ca-certificates gnupg software-properties-common ufw
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq

step "Installing dependencies"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    build-essential \
    python3.11 python3.11-venv python3.11-dev \
    nginx postgresql postgresql-contrib redis-server \
    ffmpeg certbot python3-certbot-nginx \
    openssl

step "Installing Node.js 20"
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
apt-get install -y -qq nodejs

step "Creating directories"
mkdir -p "$APP_DIR" "$HLS_DIR" "$WEB_DIR"
mkdir -p /etc/ssl/iptv /etc/nginx/snippets

step "Setting up PostgreSQL"
systemctl enable postgresql --now
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"         2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

PG_CONF=$(find /etc/postgresql -name postgresql.conf 2>/dev/null | head -1)
if [[ -f "$PG_CONF" ]]; then
    sed -i "s/^#*shared_buffers\s*=.*/shared_buffers = 512MB/"          "$PG_CONF"
    sed -i "s/^#*effective_cache_size\s*=.*/effective_cache_size = 2GB/" "$PG_CONF"
    sed -i "s/^#*max_connections\s*=.*/max_connections = 200/"           "$PG_CONF"
    systemctl restart postgresql
fi

step "Configuring Redis"
systemctl enable redis-server --now
redis-cli config set maxmemory     512mb       >/dev/null
redis-cli config set maxmemory-policy allkeys-lru >/dev/null
redis-cli config set tcp-keepalive 60          >/dev/null

step "Copying application files"
cp -r "$REPO_DIR/backend"  "$APP_DIR/"
cp -r "$REPO_DIR/nginx"    "$APP_DIR/"
cp -r "$REPO_DIR/scripts"  "$APP_DIR/"

step "Building Python 3.11 environment"
python3.11 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt" -q

step "Writing .env"
cat > "$APP_DIR/backend/.env" <<ENVEOF
DATABASE_URL=postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=$JWT_SECRET
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=30
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin
SERVER_URL=
PANEL_PORT=8000
HLS_SEGMENT_TIME=2
HLS_LIST_SIZE=6
HLS_OUTPUT_DIR=$HLS_DIR
FFMPEG_PATH=/usr/bin/ffmpeg
MAX_RETRY_ATTEMPTS=5
HEALTH_CHECK_INTERVAL=30
ADMIN_IP_WHITELIST=
RATE_LIMIT_PER_MINUTE=120
ENVEOF
chmod 600 "$APP_DIR/backend/.env"

step "Building frontend"
cd "$REPO_DIR/frontend"
npm install --silent
npm run build
cp -r dist/* "$WEB_DIR/"

step "Configuring Nginx"
mkdir -p /etc/nginx/snippets
cp "$APP_DIR/nginx/iptv-locations.conf" /etc/nginx/snippets/iptv-locations.conf
cp "$APP_DIR/nginx/iptv-panel.conf"     /etc/nginx/sites-available/iptv-panel
ln -sf /etc/nginx/sites-available/iptv-panel /etc/nginx/sites-enabled/iptv-panel
rm -f  /etc/nginx/sites-enabled/default

nginx -t 2>/dev/null && systemctl reload nginx || warn "Nginx config issue — run: nginx -t"
systemctl enable nginx

step "Installing automatic-HTTPS helper"
# Root-owned helper the backend may run via sudo to issue Let's Encrypt certs.
install -m 0755 -o root -g root "$APP_DIR/scripts/iptv-ssl-setup.sh" /usr/local/sbin/iptv-ssl-setup.sh
cat > /etc/sudoers.d/iptv-panel <<'SUDOEOF'
www-data ALL=(root) NOPASSWD: /usr/local/sbin/iptv-ssl-setup.sh
SUDOEOF
chmod 0440 /etc/sudoers.d/iptv-panel
visudo -cf /etc/sudoers.d/iptv-panel >/dev/null || { rm -f /etc/sudoers.d/iptv-panel; warn "sudoers entry invalid — auto-HTTPS disabled"; }

step "Creating systemd service"
cat > /etc/systemd/system/iptv-panel.service <<SVCEOF
[Unit]
Description=IPTV Panel Backend
After=network.target postgresql.service redis.service
Requires=postgresql.service redis.service

[Service]
Type=exec
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR/backend
Environment="PATH=$APP_DIR/venv/bin"
EnvironmentFile=$APP_DIR/backend/.env
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app \\
    --host 127.0.0.1 --port 8000 --workers 1 \\
    --loop uvloop --http httptools
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
SVCEOF

chown -R www-data:www-data "$APP_DIR" "$HLS_DIR" "$WEB_DIR"
chmod -R 755 "$HLS_DIR"

step "Kernel tuning for low-latency streaming"
cat >> /etc/sysctl.conf <<'SYSCTLEOF'
# IPTV Panel — streaming optimisation
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_wmem=4096 65536 16777216
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_congestion_control=bbr
net.core.default_qdisc=fq
net.ipv4.tcp_fastopen=3
vm.swappiness=10
SYSCTLEOF
modprobe tcp_bbr 2>/dev/null || true
sysctl -p -q    2>/dev/null || true

step "Firewall (UFW)"
ufw allow 22/tcp    >/dev/null
ufw allow 80/tcp    >/dev/null
ufw allow 443/tcp   >/dev/null
ufw allow 25461/tcp >/dev/null
ufw --force enable  >/dev/null

step "Starting IPTV Panel"
systemctl daemon-reload
systemctl enable iptv-panel
systemctl start  iptv-panel
sleep 5

if systemctl is-active --quiet iptv-panel; then
    info "Backend is running"
else
    warn "Backend may have failed — check: journalctl -u iptv-panel -n 50"
fi

# ── Detect public IP for the dashboard URL ────────────────────
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || true)
[[ -z "$SERVER_IP" ]] && SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$SERVER_IP" ]] && SERVER_IP="<your-vps-ip>"

# ── Final output ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  IPTV Panel installed!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:   ${CYAN}http://$SERVER_IP:$PANEL_PORT_HTTP${NC}"
echo -e "               ${CYAN}http://$SERVER_IP${NC}  (port 80 also works)"
echo ""
echo -e "  Username:    ${BOLD}admin${NC}"
echo -e "  Password:    ${BOLD}admin${NC}  ← you will be forced to change on first login"
echo ""
echo -e "  DB password: ${BOLD}$DB_PASS${NC}"
echo ""
echo -e "${YELLOW}  SAVE THE DB PASSWORD — you will need it for backups!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}Using a domain?${NC} Point its DNS to ${BOLD}$SERVER_IP${NC}, then set it under"
echo -e "  ${CYAN}Settings → Public Server URL${NC} in the dashboard. It is then embedded"
echo -e "  in all M3U playlists and Xtream links. For HTTPS on that domain run:"
echo -e "  ${CYAN}certbot --nginx -d your.domain.com --agree-tos -m you@email.com --non-interactive${NC}"
echo ""
echo -e "Update anytime:  ${CYAN}bash $APP_DIR/scripts/update.sh${NC}"
echo -e "Reset password:  ${CYAN}bash $APP_DIR/scripts/reset-password.sh${NC}"
