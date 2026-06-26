#!/bin/bash
# ================================================================
#  IPTV Panel — One-command installer for Ubuntu 22.04
#  Run as root on a fresh Ubuntu 22.04 VPS:  sudo bash install.sh
# ================================================================
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
CYAN=$'\033[0;36m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'
CLR=$'\033[K'

LOG="/var/log/iptv-install.log"

[[ $EUID -ne 0 ]] && { echo -e "${RED}Run as root: sudo bash install.sh${NC}"; exit 1; }
: > "$LOG"

# ── Pretty output helpers ────────────────────────────────────────
# Every heavy step runs quietly: its command output is sent to $LOG and the
# screen shows only a spinner that flips to a green ✓ when the step finishes
# (or a red ✗ + the tail of the log if it fails). The installer therefore
# reads as a clean checklist, not a wall of apt/npm noise.
run() {
    local desc="$1"; shift
    ( "$@" ) >>"$LOG" 2>&1 &
    local pid=$! frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' n=10 i=0
    printf '\r  %s%s%s %s' "$CYAN" '⠋' "$NC" "$desc"
    while kill -0 "$pid" 2>/dev/null; do
        printf '\r  %s%s%s %s' "$CYAN" "${frames:i++%n:1}" "$NC" "$desc"
        sleep 0.1
    done
    if wait "$pid"; then
        printf '\r  %s✓%s %s%s\n' "$GREEN" "$NC" "$desc" "$CLR"
    else
        printf '\r  %s✗%s %s%s\n' "$RED" "$NC" "$desc" "$CLR"
        echo -e "${RED}  ─ Step failed. Last 25 lines of ${LOG}:${NC}"
        tail -n 25 "$LOG" | sed 's/^/    /'
        echo -e "${RED}  Full log: ${LOG}${NC}"
        exit 1
    fi
}

section() { printf '\n%s%s%s\n' "$BOLD" "$1" "$NC"; }

# ── Banner ───────────────────────────────────────────────────────
clear 2>/dev/null || true
cat <<BANNER
${BOLD}${CYAN}
   ╦╔═╗╔╦╗╦  ╦  ╔═╗╔═╗╔╗╔╔═╗╦
   ║╠═╝ ║ ╚╗╔╝  ╠═╝╠═╣║║║║╣ ║
   ╩╩   ╩  ╚╝   ╩  ╩ ╩╝╚╝╚═╝╩═╝
${NC}${DIM}   Self-hosted IPTV dashboard installer${NC}
${DIM}   Target: Ubuntu 22.04 · Log: ${LOG}${NC}

BANNER

# Fully non-interactive apt: no debconf dialogs, no needrestart prompts, keep
# existing config files on upgrades.
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1
APT_OPTS=(-o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef)

# ── Config ───────────────────────────────────────────────────────
APP_DIR="/opt/iptv-panel"
HLS_DIR="/var/iptv/hls"
WEB_DIR="/var/www/iptv-panel"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PANEL_PORT_HTTP=25461   # dashboard / Xtream port (Xtream Codes default)

DB_NAME="iptvpanel"
DB_USER="iptv"
DB_PASS=$(openssl rand -hex 20)
JWT_SECRET=$(openssl rand -hex 32)
# Admin password: 6 random lowercase letters, unique to THIS install — there is
# no shared "admin/admin" default, so no two servers get the same one. Shown
# once, at the end. The panel then forces the admin to set their own password
# on first login (username stays "admin").
ADMIN_PASS=$(LC_ALL=C tr -dc 'a-z' < /dev/urandom 2>/dev/null | head -c 6 || true)
[[ ${#ADMIN_PASS} -eq 6 ]] || ADMIN_PASS=$(openssl rand -hex 3)

# ================================================================
section "System"
# ================================================================
do_update()   { apt-get update -qq; apt-get upgrade -y -qq "${APT_OPTS[@]}"; }
do_basetool() {
    apt-get install -y -qq "${APT_OPTS[@]}" \
        curl wget git unzip ca-certificates gnupg software-properties-common ufw
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
}
do_deps() {
    apt-get install -y -qq "${APT_OPTS[@]}" \
        build-essential \
        python3.11 python3.11-venv python3.11-dev \
        nginx postgresql postgresql-contrib redis-server \
        ffmpeg certbot python3-certbot-nginx openssl
}
do_node() {
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq "${APT_OPTS[@]}" nodejs
}
do_ytdlp() {
    curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
        -o /usr/local/bin/yt-dlp
    chmod a+rx /usr/local/bin/yt-dlp
    cat > /etc/cron.d/iptv-ytdlp <<'CRONEOF'
0 3 * * * root /usr/local/bin/yt-dlp -U >/dev/null 2>&1
CRONEOF
    chmod 0644 /etc/cron.d/iptv-ytdlp
    if ! command -v deno >/dev/null 2>&1; then
        curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y || true
    fi
}
run "Updating system packages"           do_update
run "Installing base tools + Python 3.11" do_basetool
run "Installing core dependencies (nginx, postgres, redis, ffmpeg)" do_deps
run "Installing Node.js 20"              do_node
run "Installing yt-dlp + deno (YouTube resolver)" do_ytdlp

# ================================================================
section "Storage & data"
# ================================================================
do_dirs() { mkdir -p "$APP_DIR" "$HLS_DIR" "$WEB_DIR" /etc/ssl/iptv /etc/nginx/snippets; }
do_tmpfs() {
    # Serve HLS segments from a RAM disk — the single biggest anti-buffering win
    # (no disk I/O on the hot path). 2 GB, owned by www-data (uid/gid 33).
    mkdir -p "$HLS_DIR"
    grep -q " $HLS_DIR " /etc/fstab || \
        echo "tmpfs $HLS_DIR tmpfs defaults,size=2G,mode=0755,uid=33,gid=33,noatime 0 0" >> /etc/fstab
    mountpoint -q "$HLS_DIR" || mount "$HLS_DIR"
}
do_postgres() {
    systemctl enable postgresql --now
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"        2>/dev/null || true
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
    local pg_conf
    pg_conf=$(find /etc/postgresql -name postgresql.conf 2>/dev/null | head -1)
    if [[ -f "$pg_conf" ]]; then
        sed -i "s/^#*shared_buffers\s*=.*/shared_buffers = 512MB/"           "$pg_conf"
        sed -i "s/^#*effective_cache_size\s*=.*/effective_cache_size = 2GB/" "$pg_conf"
        sed -i "s/^#*max_connections\s*=.*/max_connections = 200/"           "$pg_conf"
        systemctl restart postgresql
    fi
}
do_redis() {
    systemctl enable redis-server --now
    redis-cli config set maxmemory        512mb        >/dev/null
    redis-cli config set maxmemory-policy allkeys-lru   >/dev/null
    redis-cli config set tcp-keepalive    60           >/dev/null
}
run "Creating directories"            do_dirs
run "Mounting HLS RAM disk (tmpfs)"   do_tmpfs
run "Configuring PostgreSQL"          do_postgres
run "Configuring Redis"               do_redis

# ================================================================
section "Application"
# ================================================================
do_copy() {
    cp -r "$REPO_DIR/backend" "$APP_DIR/"
    cp -r "$REPO_DIR/nginx"   "$APP_DIR/"
    cp -r "$REPO_DIR/scripts" "$APP_DIR/"
}
do_venv() {
    python3.11 -m venv "$APP_DIR/venv"
    "$APP_DIR/venv/bin/pip" install --upgrade pip -q
    "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt" -q
}
do_env() {
    cat > "$APP_DIR/backend/.env" <<ENVEOF
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
YTDLP_PATH=/usr/local/bin/yt-dlp
YTDLP_COOKIES=
YTDLP_PROXY=
MAX_RETRY_ATTEMPTS=5
HEALTH_CHECK_INTERVAL=30
ADMIN_IP_WHITELIST=
RATE_LIMIT_PER_MINUTE=120
ENVEOF
    chmod 600 "$APP_DIR/backend/.env"
}
do_frontend() {
    cd "$REPO_DIR/frontend"
    npm install --silent
    npm run build
    cp -r dist/* "$WEB_DIR/"
}
run "Copying application files"       do_copy
run "Building Python 3.11 environment" do_venv
run "Writing configuration (.env)"    do_env
run "Building the dashboard (frontend)" do_frontend

# ================================================================
section "Web server & services"
# ================================================================
do_nginx() {
    mkdir -p /etc/nginx/snippets
    cp "$APP_DIR/nginx/iptv-locations.conf" /etc/nginx/snippets/iptv-locations.conf
    cp "$APP_DIR/nginx/iptv-panel.conf"     /etc/nginx/sites-available/iptv-panel
    [[ -f /etc/nginx/snippets/iptv-redirect.conf ]] || \
        cp "$APP_DIR/nginx/iptv-redirect.conf" /etc/nginx/snippets/iptv-redirect.conf
    ln -sf /etc/nginx/sites-available/iptv-panel /etc/nginx/sites-enabled/iptv-panel
    rm -f /etc/nginx/sites-enabled/default
    # Connection-handling tuning in the main nginx.conf (events block).
    local nc=/etc/nginx/nginx.conf
    cp -a "$nc" "$nc.bak.install" 2>/dev/null || true
    sed -i -E 's/(^[[:space:]]*worker_connections[[:space:]]+)[0-9]+;/\14096;/' "$nc"
    grep -qE '^[[:space:]]*multi_accept' "$nc" || \
        sed -i -E '0,/^[[:space:]]*events[[:space:]]*\{/s//&\n    multi_accept on;/' "$nc"
    nginx -t
    systemctl enable nginx
    systemctl reload nginx
}
do_ssl_helper() {
    install -m 0755 -o root -g root "$APP_DIR/scripts/iptv-ssl-setup.sh" /usr/local/sbin/iptv-ssl-setup.sh
    cat > /etc/sudoers.d/iptv-panel <<'SUDOEOF'
www-data ALL=(root) NOPASSWD: /usr/local/sbin/iptv-ssl-setup.sh
SUDOEOF
    chmod 0440 /etc/sudoers.d/iptv-panel
    visudo -cf /etc/sudoers.d/iptv-panel >/dev/null || { rm -f /etc/sudoers.d/iptv-panel; echo "sudoers entry invalid"; }
}
do_service() {
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
Environment="PATH=$APP_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
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
    systemctl daemon-reload
}
do_hls_clean() {
    # Reap empty/zero-byte segments a crashed ffmpeg can leave behind so the
    # player never fetches a 0-byte .ts.
    cat > /etc/systemd/system/hls-clean-empty.service <<'EOF'
[Unit]
Description=Remove stale empty HLS segments
[Service]
Type=oneshot
ExecStart=/usr/bin/find /var/iptv/hls -type f -name "*.ts" -size 0 -mmin +1 -delete
EOF
    cat > /etc/systemd/system/hls-clean-empty.timer <<'EOF'
[Unit]
Description=Periodically reap stale empty HLS segments
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now hls-clean-empty.timer
}
run "Configuring Nginx (+ connection tuning)" do_nginx
run "Installing automatic-HTTPS helper"       do_ssl_helper
run "Creating the panel service"              do_service
run "Enabling HLS cleanup timer"              do_hls_clean

# ================================================================
section "Tuning & security"
# ================================================================
do_sysctl() {
    if ! grep -q "IPTV Panel — streaming" /etc/sysctl.conf; then
        cat >> /etc/sysctl.conf <<'SYSCTLEOF'
# IPTV Panel — streaming optimisation
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_wmem=4096 65536 16777216
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_congestion_control=bbr
net.core.default_qdisc=fq
net.ipv4.tcp_fastopen=3
net.core.somaxconn=4096
net.core.netdev_max_backlog=5000
net.ipv4.tcp_fin_timeout=30
vm.swappiness=10
SYSCTLEOF
    fi
    modprobe tcp_bbr 2>/dev/null || true
    sysctl -p -q 2>/dev/null || true
}
do_firewall() {
    ufw allow 22/tcp    >/dev/null
    ufw allow 80/tcp    >/dev/null
    ufw allow 443/tcp   >/dev/null
    ufw allow 25461/tcp >/dev/null
    ufw allow 8080/tcp  >/dev/null
    ufw --force enable  >/dev/null
}
do_start() {
    systemctl enable iptv-panel
    systemctl start  iptv-panel
    sleep 5
    systemctl is-active --quiet iptv-panel
}
run "Applying kernel network tuning"  do_sysctl
run "Configuring firewall (UFW)"      do_firewall
run "Starting IPTV Panel"             do_start

# ── Detect public IP for the dashboard URL ───────────────────────
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || true)
[[ -z "$SERVER_IP" ]] && SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$SERVER_IP" ]] && SERVER_IP="<your-vps-ip>"

# ── Final output ─────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  ✓ IPTV Panel installed${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:   ${CYAN}http://$SERVER_IP:$PANEL_PORT_HTTP${NC}"
echo -e "               ${CYAN}http://$SERVER_IP${NC}  (port 80 also works)"
echo ""
echo -e "  Username:    ${RED}${BOLD}admin${NC}"
echo -e "  Password:    ${RED}${BOLD}$ADMIN_PASS${NC}"
echo ""
echo -e "  DB password: ${RED}${BOLD}$DB_PASS${NC}"
echo ""
echo -e "${YELLOW}  COPY THESE NOW — the admin password is shown ONLY here (it is not on${NC}"
echo -e "${YELLOW}  the login page). On first login you'll be asked to set your own${NC}"
echo -e "${YELLOW}  password. Keep the DB password for backups.${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "${DIM}Using a domain?${NC} Point its DNS to ${BOLD}$SERVER_IP${NC}, set it under"
echo -e "  ${CYAN}Settings → Public Server URL${NC} in the dashboard, then for HTTPS run:"
echo -e "  ${CYAN}certbot --nginx -d your.domain.com --agree-tos -m you@email.com --non-interactive${NC}"
echo ""
echo -e "Update anytime:  ${CYAN}bash $APP_DIR/scripts/update.sh${NC}"
echo -e "Reset password:  ${CYAN}bash $APP_DIR/scripts/reset-password.sh${NC}"
echo -e "Install log:     ${CYAN}$LOG${NC}"
