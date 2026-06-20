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
REPO_URL="https://github.com/justkeitany/Kobe_stores.git"
TMP_DIR="/tmp/mzeekobe-update-$$"

# If repo is private, pass token as env var: GH_TOKEN=xxx bash update.sh
if [[ -n "${GH_TOKEN:-}" ]]; then
    REPO_URL="https://$GH_TOKEN@github.com/justkeitany/Kobe_stores.git"
fi

step "Pulling latest code"
git clone --depth=1 "$REPO_URL" "$TMP_DIR"
info "Cloned latest version"

step "Ensuring yt-dlp is installed (YouTube live resolver)"
if [[ ! -x /usr/local/bin/yt-dlp ]]; then
    curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
        -o /usr/local/bin/yt-dlp
    chmod a+rx /usr/local/bin/yt-dlp
    info "yt-dlp installed"
else
    info "yt-dlp already present"
fi
# Nightly self-update cron (idempotent)
if [[ ! -f /etc/cron.d/iptv-ytdlp ]]; then
    cat > /etc/cron.d/iptv-ytdlp <<'CRONEOF'
0 3 * * * root /usr/local/bin/yt-dlp -U >/dev/null 2>&1
CRONEOF
    chmod 0644 /etc/cron.d/iptv-ytdlp
    info "yt-dlp nightly update cron added"
fi
# Deno: JS runtime yt-dlp needs for reliable YouTube extraction.
if ! command -v deno >/dev/null 2>&1; then
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y >/dev/null 2>&1 && info "deno installed" || warn "deno install failed"
fi

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
mkdir -p /etc/nginx/snippets
cp "$TMP_DIR/nginx/iptv-panel.conf"     /etc/nginx/sites-available/iptv-panel
cp "$TMP_DIR/nginx/iptv-locations.conf" /etc/nginx/snippets/iptv-locations.conf
# Raw-IP redirect control: install the empty default ONLY if absent, so a
# domain lock written by iptv-ssl-setup.sh (return 301 to the user's domain)
# is preserved across upgrades rather than reverted to open IP access.
[[ -f /etc/nginx/snippets/iptv-redirect.conf ]] || \
    cp "$TMP_DIR/nginx/iptv-redirect.conf" /etc/nginx/snippets/iptv-redirect.conf
nginx -t 2>/dev/null && systemctl reload nginx
info "Nginx reloaded"

step "Updating HTTPS helper"
install -m 0755 -o root -g root "$TMP_DIR/scripts/iptv-ssl-setup.sh" /usr/local/sbin/iptv-ssl-setup.sh
if [[ ! -f /etc/sudoers.d/iptv-panel ]]; then
    echo 'www-data ALL=(root) NOPASSWD: /usr/local/sbin/iptv-ssl-setup.sh' > /etc/sudoers.d/iptv-panel
    chmod 0440 /etc/sudoers.d/iptv-panel
    visudo -cf /etc/sudoers.d/iptv-panel >/dev/null || rm -f /etc/sudoers.d/iptv-panel
fi
info "HTTPS helper updated"

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
