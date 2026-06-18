#!/bin/bash
# ============================================================
# IPTV Panel — Emergency Password Reset
# Run on the VPS: sudo bash scripts/reset-password.sh
# ============================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

[[ $EUID -ne 0 ]] && echo -e "${RED}Run as root: sudo bash reset-password.sh${NC}" && exit 1

APP_DIR="/opt/iptv-panel"
ENV_FILE="$APP_DIR/backend/.env"

echo -e "${CYAN}"
echo "================================================"
echo "   IPTV Panel — Password Reset"
echo "================================================"
echo -e "${NC}"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}ERROR: .env file not found at $ENV_FILE${NC}"
    exit 1
fi

# Prompt for new password
while true; do
    read -s -p "Enter new admin password (min 8 chars): " NEW_PASS
    echo
    if [ ${#NEW_PASS} -lt 8 ]; then
        echo -e "${RED}Password too short. Must be at least 8 characters.${NC}"
        continue
    fi
    read -s -p "Confirm new password: " CONFIRM_PASS
    echo
    if [ "$NEW_PASS" != "$CONFIRM_PASS" ]; then
        echo -e "${RED}Passwords do not match. Try again.${NC}"
        continue
    fi
    break
done

# Update .env file
sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$NEW_PASS/" "$ENV_FILE"
echo -e "${GREEN}✓ .env updated${NC}"

# Clear the force-change flag so the operator is prompted to set a new password
# on next login. The backend uses the key "credentials_changed" (app/auth.py).
if command -v redis-cli &>/dev/null; then
    redis-cli DEL credentials_changed >/dev/null 2>&1 && \
        echo -e "${GREEN}✓ Redis flag cleared${NC}" || \
        echo -e "${YELLOW}⚠ Could not clear Redis flag (Redis may not be running)${NC}"
fi

# Restart the backend so it picks up the new password
systemctl restart iptv-panel && \
    echo -e "${GREEN}✓ Backend restarted${NC}" || \
    echo -e "${YELLOW}⚠ Could not restart service — restart manually: systemctl restart iptv-panel${NC}"

SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || true)
[[ -z "$SERVER_IP" ]] && SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$SERVER_IP" ]] && SERVER_IP="<your-vps-ip>"

# Show the actual configured username (it may have been changed from "admin").
CUR_USER=$(grep -E '^ADMIN_USERNAME=' "$ENV_FILE" | head -1 | cut -d= -f2-)
[[ -z "$CUR_USER" ]] && CUR_USER="admin"

echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN} Password reset complete!${NC}"
echo -e "${GREEN} Login at:  http://$SERVER_IP:25461${NC}"
echo -e "${GREEN} Username:  $CUR_USER${NC}"
echo -e "${GREEN} Password:  $NEW_PASS${NC}"
echo -e "${GREEN}================================================${NC}"
echo -e "${YELLOW} You will be prompted to set a new password on first login.${NC}"
