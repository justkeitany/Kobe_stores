#!/bin/bash
# ================================================================
#  IPTV Panel — Live update script
#  Pulls latest code from GitHub and redeploys without downtime
#  Usage: sudo bash /opt/iptv-panel/scripts/update.sh
# ================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
step() { echo -e "\n${CYAN}══ $1 ══${NC}"; }

[[ $EUID -ne 0 ]] && echo "Run as root: sudo bash update.sh" && exit 1

APP_DIR="/opt/iptv-panel"
WEB_DIR="/var/www/iptv-panel"
REPO_URL="https://github.com/keitanyfrank/mzeekobe.git"
TMP_DIR="/tmp/mzeekobe-update-$$"

step "Pulling latest code"
git clone --depth=1 "$REPO_URL" "$TMP_DIR"
info "Cloned latest version"

step "Updating backend"
# Copy new app code but preserve .env
cp -r "$TMP_DIR/backend/app" "$APP_DIR/backend/"
cp    "$TMP_DIR/backend/requirements.txt" "$APP_DIR/backend/"
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt" -q
info "Backend updated"

step "Building frontend"
cd "$TMP_DIR/frontend"
npm install --silent
npm run build
rm -rf "$WEB_DIR"/*
cp -r dist/* "$WEB_DIR/"
info "Frontend built and deployed"

step "Updating Nginx config"
cp "$TMP_DIR/nginx/iptv-panel.conf"     /etc/nginx/sites-available/iptv-panel
cp "$TMP_DIR/nginx/iptv-locations.conf" /etc/nginx/snippets/iptv-locations.conf
nginx -t 2>/dev/null && systemctl reload nginx
info "Nginx reloaded"

step "Restarting backend"
chown -R www-data:www-data "$APP_DIR" "$WEB_DIR"
systemctl restart iptv-panel
sleep 3

if systemctl is-active --quiet iptv-panel; then
    info "Backend restarted successfully"
else
    warn "Backend may have failed — check: journalctl -u iptv-panel -n 50"
fi

rm -rf "$TMP_DIR"
echo ""
echo -e "${GREEN}Update complete!${NC}"
systemctl status iptv-panel --no-pager -l
