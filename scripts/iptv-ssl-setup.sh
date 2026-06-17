#!/bin/bash
# ================================================================
#  IPTV Panel — automatic HTTPS setup for a domain
#
#  Installed to /usr/local/sbin/iptv-ssl-setup.sh (root:root 0755).
#  Invoked by the backend (running as www-data) via sudo:
#
#     sudo /usr/local/sbin/iptv-ssl-setup.sh <domain>
#
#  Creates an Nginx server block for the domain, obtains a Let's
#  Encrypt certificate with certbot, and enables HTTP->HTTPS redirect.
#  Renewal is handled by certbot's own systemd timer.
#
#  Exit 0 on success; non-zero with a message on stderr otherwise.
# ================================================================
set -euo pipefail

DOMAIN="${1:-}"

if [[ -z "$DOMAIN" ]]; then
    echo "ERROR: no domain provided" >&2
    exit 2
fi

# Strict validation — defends against injection into nginx/shell/certbot.
if ! [[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$ ]]; then
    echo "ERROR: invalid domain: $DOMAIN" >&2
    exit 2
fi
if (( ${#DOMAIN} > 253 )); then
    echo "ERROR: domain too long" >&2
    exit 2
fi

SITE_FILE="/etc/nginx/sites-available/iptv-${DOMAIN}"
SITE_LINK="/etc/nginx/sites-enabled/iptv-${DOMAIN}"

echo "Configuring Nginx server block for ${DOMAIN}..."
cat > "$SITE_FILE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    access_log /var/log/nginx/iptv-access.log combined buffer=16k flush=5s;
    error_log  /var/log/nginx/iptv-error.log warn;

    include snippets/iptv-locations.conf;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain application/json application/javascript text/css application/vnd.apple.mpegurl;
}
NGINX

ln -sf "$SITE_FILE" "$SITE_LINK"

if ! nginx -t 2>/dev/null; then
    echo "ERROR: nginx config test failed" >&2
    exit 3
fi
systemctl reload nginx

echo "Requesting Let's Encrypt certificate for ${DOMAIN}..."
if ! certbot --nginx \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --register-unsafely-without-email \
        --redirect \
        --keep-until-expiring 2>&1; then
    echo "ERROR: certbot failed for ${DOMAIN} (check that DNS points to this server)" >&2
    exit 4
fi

systemctl reload nginx
echo "SUCCESS: HTTPS enabled for ${DOMAIN}"
exit 0
