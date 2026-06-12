#!/bin/bash
# ================================================================
#  IPTV Panel — One-command installer for Ubuntu 22.04
#  Usage: bash install.sh
#  Repo:  https://github.com/keitanyfrank/mzeekobe
# ================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗] $1${NC}"; exit 1; }
step()  { echo -e "\n${CYAN}${BOLD}══ $1 ══${NC}"; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash install.sh"
[[ $(lsb_release -rs) != "22.04" ]] && warn "Tested on Ubuntu 22.04. Continuing anyway..."

# ── Config ───────────────────────────────────────────────────
APP_DIR="/opt/iptv-panel"
HLS_DIR="/var/iptv/hls"
WEB_DIR="/var/www/iptv-panel"
REPO_DIR="/tmp/mzeekobe"
DOMAIN="${DOMAIN:-live.keitanyfrank.store}"
DB_NAME="iptvpanel"
DB_USER="iptv"
DB_PASS=$(openssl rand -hex 20)
JWT_SECRET=$(openssl rand -hex 32)

step "System update"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

step "Installing dependencies"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget git unzip software-properties-common \
    build-essential \
    python3.11 python3.11-venv python3.11-dev \
    nginx postgresql postgresql-contrib redis-server \
    ffmpeg certbot python3-certbot-nginx \
    openssl ufw

step "Installing Node.js 20"
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
apt-get install -y -qq nodejs

step "Creating directories"
mkdir -p "$APP_DIR" "$HLS_DIR" "$WEB_DIR"
mkdir -p /etc/ssl/iptv /etc/nginx/snippets

step "Setting up PostgreSQL"
systemctl enable postgresql --now
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# Tune PG
PG_CONF=$(find /etc/postgresql -name postgresql.conf 2>/dev/null | head -1)
if [[ -f "$PG_CONF" ]]; then
    sed -i "s/^#*shared_buffers\s*=.*/shared_buffers = 256MB/"       "$PG_CONF"
    sed -i "s/^#*effective_cache_size\s*=.*/effective_cache_size = 1GB/" "$PG_CONF"
    sed -i "s/^#*max_connections\s*=.*/max_connections = 100/"       "$PG_CONF"
    systemctl restart postgresql
fi

step "Configuring Redis"
systemctl enable redis-server --now
redis-cli config set maxmemory 256mb >/dev/null
redis-cli config set maxmemory-policy allkeys-lru >/dev/null
redis-cli config set tcp-keepalive 60 >/dev/null

step "Copying application files"
cp -r "$REPO_DIR/backend"  "$APP_DIR/"
cp -r "$REPO_DIR/nginx"    "$APP_DIR/"
cp -r "$REPO_DIR/scripts"  "$APP_DIR/"

step "Building Python environment (Python 3.11)"
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
SERVER_URL=https://$DOMAIN
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
# Self-signed cert for initial setup
if [[ ! -f /etc/ssl/iptv/cert.pem ]]; then
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout /etc/ssl/iptv/key.pem \
        -out /etc/ssl/iptv/cert.pem \
        -subj "/CN=$DOMAIN" 2>/dev/null
fi
cp "$APP_DIR/nginx/iptv-panel.conf"    /etc/nginx/sites-available/iptv-panel
cp "$APP_DIR/nginx/iptv-locations.conf" /etc/nginx/snippets/iptv-locations.conf
ln -sf /etc/nginx/sites-available/iptv-panel /etc/nginx/sites-enabled/iptv-panel
rm -f /etc/nginx/sites-enabled/default

# Replace localhost placeholder in nginx config
sed -i "s/live\.keitanyfrank\.store/$DOMAIN/g" /etc/nginx/sites-available/iptv-panel

nginx -t 2>/dev/null && systemctl reload nginx || warn "Nginx config issue — check: nginx -t"
systemctl enable nginx

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
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2 --loop uvloop --http httptools
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
# IPTV Panel
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
sysctl -p -q 2>/dev/null || true

step "Firewall (UFW)"
ufw allow 22/tcp  >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

step "Starting IPTV Panel"
systemctl daemon-reload
systemctl enable iptv-panel
systemctl start iptv-panel
sleep 4

if systemctl is-active --quiet iptv-panel; then
    info "Backend is running"
else
    warn "Backend may have failed to start"
    warn "Check: journalctl -u iptv-panel -n 50"
fi

# ── SSL with Let's Encrypt ────────────────────────────────────
echo ""
echo -e "${YELLOW}To get a free SSL certificate run:${NC}"
echo "  certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m admin@$DOMAIN"

# ── Print credentials ─────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════════${NC}"
echo -e "${BOLD}  IPTV Panel installed successfully!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════════${NC}"
echo ""
echo -e "  Panel URL:   ${CYAN}https://$DOMAIN${NC}"
echo -e "  Username:    ${BOLD}admin${NC}"
echo -e "  Password:    ${BOLD}admin${NC}  ← change on first login"
echo ""
echo -e "  Xtream URL:  ${CYAN}https://$DOMAIN${NC}"
echo ""
echo -e "  DB password: $DB_PASS"
echo -e "  JWT secret:  (saved in $APP_DIR/backend/.env)"
echo ""
echo -e "${YELLOW}  SAVE THESE CREDENTIALS NOW!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════════${NC}"
echo ""
echo -e "To update later:  ${CYAN}bash $APP_DIR/scripts/update.sh${NC}"
echo -e "Reset password:   ${CYAN}bash $APP_DIR/scripts/reset-password.sh${NC}"
