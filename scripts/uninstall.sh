#!/bin/bash
# ================================================================
#  IPTV Panel — uninstall / clean slate
#
#  Removes the service, app files, nginx config, SSL helper and
#  (by default) the database, so you can reinstall fresh.
#
#  Run as root:
#    curl -fsSL https://raw.githubusercontent.com/justkeitany/Kobe_stores/main/scripts/uninstall.sh | sudo bash
#
#  Keep the database (streams/users/settings):  KEEP_DB=1 sudo bash uninstall.sh
#  Also remove Let's Encrypt certificates:      PURGE_CERTS=1 sudo bash uninstall.sh
# ================================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

[[ $EUID -ne 0 ]] && echo -e "${RED}Run as root (use sudo)${NC}" && exit 1

info "Stopping and removing the service..."
systemctl stop iptv-panel    2>/dev/null || true
systemctl disable iptv-panel 2>/dev/null || true
rm -f /etc/systemd/system/iptv-panel.service
systemctl daemon-reload 2>/dev/null || true

info "Removing Nginx config..."
rm -f /etc/nginx/sites-enabled/iptv-panel  /etc/nginx/sites-available/iptv-panel
# Per-domain server blocks created for HTTPS (iptv-<domain>)
rm -f /etc/nginx/sites-enabled/iptv-*      /etc/nginx/sites-available/iptv-*
rm -f /etc/nginx/snippets/iptv-locations.conf
systemctl reload nginx 2>/dev/null || true

info "Removing HTTPS helper and sudoers entry..."
rm -f /usr/local/sbin/iptv-ssl-setup.sh
rm -f /etc/sudoers.d/iptv-panel

info "Removing application files..."
rm -rf /opt/iptv-panel /opt/iptv-panel-src /tmp/mzeekobe /tmp/mzeekobe-update-* \
       /var/www/iptv-panel /var/iptv/hls

if [[ "${KEEP_DB:-}" == "1" ]]; then
    warn "KEEP_DB=1 — leaving the database in place"
else
    info "Dropping database and user..."
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS iptvpanel;" 2>/dev/null || true
    sudo -u postgres psql -c "DROP USER IF EXISTS iptv;"          2>/dev/null || true
fi

info "Clearing Redis flags..."
if command -v redis-cli &>/dev/null; then
    redis-cli DEL credentials_changed password_changed >/dev/null 2>&1 || true
    redis-cli --scan --pattern 'login_attempts:*' 2>/dev/null | xargs -r redis-cli DEL >/dev/null 2>&1 || true
fi

if [[ "${PURGE_CERTS:-}" == "1" ]]; then
    warn "PURGE_CERTS=1 — removing Let's Encrypt certificates"
    rm -rf /etc/letsencrypt/live/* /etc/letsencrypt/archive/* /etc/letsencrypt/renewal/* 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN} IPTV Panel removed.${NC}"
echo -e " Reinstall the latest with:"
echo -e "   ${YELLOW}curl -fsSL https://raw.githubusercontent.com/justkeitany/Kobe_stores/main/scripts/bootstrap.sh | sudo bash${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
