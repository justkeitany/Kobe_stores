#!/bin/bash
# ============================================================
# IPTV Panel — Ubuntu 22.04 Install Script
# Domain: tv.keitanyfrank.store
# Run as root: sudo bash install.sh
# ============================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash install.sh"

# ── Config ───────────────────────────────────────────────────
PANEL_PORT_HTTP=8080   # dashboard / Xtream port advertised to users
APP_DIR="/opt/iptv-panel"
HLS_DIR="/var/iptv/hls"
WEB_DIR="/var/www/iptv-panel"
DB_NAME="iptvpanel"
DB_USER="iptv"
DB_PASS=$(openssl rand -hex 16)
JWT_SECRET=$(openssl rand -hex 32)
ADMIN_PASS=$(openssl rand -base64 12)

info "Starting IPTV Panel installation..."

# ── System update ─────────────────────────────────────────────
info "Updating system..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

# Tools needed to add the deadsnakes PPA (Python 3.11 is not in Ubuntu 22.04's
# default repositories, so we must enable it before installing python3.11).
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget git unzip ca-certificates gnupg software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq

info "Installing dependencies..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    build-essential python3.11 python3.11-venv python3.11-dev python3-pip \
    nginx postgresql postgresql-contrib redis-server \
    ffmpeg certbot python3-certbot-nginx \
    openssl

# ── Node.js 20 ───────────────────────────────────────────────
info "Installing Node.js 20..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

# ── Directories ───────────────────────────────────────────────
info "Creating directories..."
mkdir -p "$APP_DIR" "$HLS_DIR" "$WEB_DIR"
mkdir -p /etc/ssl/iptv
chmod 755 "$HLS_DIR" "$WEB_DIR"

# ── PostgreSQL ────────────────────────────────────────────────
info "Setting up PostgreSQL..."
systemctl enable postgresql
systemctl start postgresql

sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || warn "User may already exist"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || warn "DB may already exist"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# Tune PostgreSQL for streaming workload
PG_CONF="/etc/postgresql/14/main/postgresql.conf"
if [ -f "$PG_CONF" ]; then
    sed -i "s/#shared_buffers = .*/shared_buffers = 256MB/" "$PG_CONF"
    sed -i "s/#effective_cache_size = .*/effective_cache_size = 1GB/" "$PG_CONF"
    sed -i "s/#max_connections = .*/max_connections = 200/" "$PG_CONF"
    systemctl restart postgresql
fi

# ── Redis ─────────────────────────────────────────────────────
info "Configuring Redis..."
systemctl enable redis-server
systemctl start redis-server
# Tune for low latency
redis-cli config set maxmemory 256mb
redis-cli config set maxmemory-policy allkeys-lru
redis-cli config set tcp-keepalive 60

# ── Copy application ──────────────────────────────────────────
info "Copying application files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cp -r "$PROJECT_DIR/backend" "$APP_DIR/"
cp -r "$PROJECT_DIR/nginx" "$APP_DIR/"
cp -r "$PROJECT_DIR/scripts" "$APP_DIR/"

# ── Python venv ───────────────────────────────────────────────
info "Setting up Python 3.11 environment..."
python3.11 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt" -q

# ── Environment file ──────────────────────────────────────────
info "Creating .env file..."
cat > "$APP_DIR/backend/.env" <<EOF
DATABASE_URL=postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=$JWT_SECRET
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=30
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$ADMIN_PASS
SERVER_URL=
PANEL_PORT=8000
HLS_SEGMENT_TIME=2
HLS_LIST_SIZE=6
HLS_OUTPUT_DIR=$HLS_DIR
FFMPEG_PATH=/usr/bin/ffmpeg
MAX_RETRY_ATTEMPTS=5
HEALTH_CHECK_INTERVAL=30
RATE_LIMIT_PER_MINUTE=60
EOF
chmod 600 "$APP_DIR/backend/.env"

# ── Build frontend ────────────────────────────────────────────
info "Building frontend..."
cd "$PROJECT_DIR/frontend"
npm install --silent
VITE_API_URL="" npm run build
cp -r dist/* "$WEB_DIR/"

# ── Nginx ─────────────────────────────────────────────────────
info "Configuring Nginx..."

cp "$APP_DIR/nginx/iptv-panel.conf" /etc/nginx/sites-available/iptv-panel
ln -sf /etc/nginx/sites-available/iptv-panel /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Tune Nginx for streaming
cat >> /etc/nginx/nginx.conf <<'NGINXEOF' 2>/dev/null || true
# Streaming tuning (appended by iptv install script)
NGINXEOF

nginx -t && systemctl reload nginx || warn "Nginx config error — check manually"
systemctl enable nginx

# ── Systemd service ───────────────────────────────────────────
info "Creating systemd service..."
cat > /etc/systemd/system/iptv-panel.service <<EOF
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
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2 --loop uvloop --http httptools --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Resource limits
LimitNOFILE=65535
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
EOF

# Fix permissions
chown -R www-data:www-data "$APP_DIR" "$HLS_DIR" "$WEB_DIR"
chmod -R 755 "$HLS_DIR"

# ── Kernel tuning for low latency ────────────────────────────
info "Tuning kernel for low-latency streaming..."
cat >> /etc/sysctl.conf <<'EOF'
# IPTV Panel tuning
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_wmem=4096 65536 16777216
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_congestion_control=bbr
net.core.default_qdisc=fq
net.ipv4.tcp_fastopen=3
vm.swappiness=10
EOF
sysctl -p -q 2>/dev/null || true

# Enable BBR TCP congestion control (reduces buffering)
modprobe tcp_bbr 2>/dev/null || true

# ── Firewall ──────────────────────────────────────────────────
if command -v ufw &>/dev/null; then
    info "Configuring firewall (UFW)..."
    ufw allow 22/tcp   >/dev/null 2>&1 || true
    ufw allow 80/tcp   >/dev/null 2>&1 || true
    ufw allow 443/tcp  >/dev/null 2>&1 || true
    ufw allow 8080/tcp >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true
fi

# ── Start services ────────────────────────────────────────────
info "Starting services..."
systemctl daemon-reload
systemctl enable iptv-panel
systemctl start iptv-panel

sleep 3
if systemctl is-active --quiet iptv-panel; then
    info "Backend started successfully"
else
    warn "Backend may not have started — check: journalctl -u iptv-panel -n 50"
fi

# ── Detect public IP for the dashboard URL ────────────────────
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || true)
[[ -z "$SERVER_IP" ]] && SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$SERVER_IP" ]] && SERVER_IP="<your-vps-ip>"

# ── Print credentials ─────────────────────────────────────────
echo ""
echo "================================================================"
echo " IPTV Panel installed successfully!"
echo "================================================================"
echo " Dashboard:     http://$SERVER_IP:$PANEL_PORT_HTTP"
echo "                http://$SERVER_IP  (port 80 also works)"
echo " Admin user:    admin"
echo " Admin pass:    $ADMIN_PASS"
echo ""
echo " Xtream Server: http://$SERVER_IP:$PANEL_PORT_HTTP"
echo " Username:      admin"
echo " Password:      $ADMIN_PASS"
echo ""
echo " M3U URL:       http://$SERVER_IP:$PANEL_PORT_HTTP/get.php?username=admin&password=$ADMIN_PASS&type=m3u_plus"
echo " XMLTV URL:     http://$SERVER_IP:$PANEL_PORT_HTTP/xmltv.php?username=admin&password=$ADMIN_PASS"
echo ""
echo " DB password:   $DB_PASS"
echo " JWT secret:    (stored in $APP_DIR/backend/.env)"
echo "================================================================"
echo " SAVE THESE CREDENTIALS NOW!"
echo ""
echo " Using a domain? Point its DNS at $SERVER_IP, then set it under"
echo " Settings -> Public Server URL in the dashboard. For HTTPS run:"
echo "   certbot --nginx -d your.domain.com --agree-tos -m you@email.com --non-interactive"
echo "================================================================"
