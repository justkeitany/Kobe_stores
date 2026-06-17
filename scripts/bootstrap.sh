#!/bin/bash
# ================================================================
#  IPTV Panel — one-command remote installer
#
#  Run on a fresh Ubuntu 22.04 VPS as root:
#
#    curl -fsSL https://raw.githubusercontent.com/justkeitany/Kobe_stores/main/scripts/bootstrap.sh | sudo bash
#
#  No domain needed — the dashboard opens at http://<VPS-IP>:25461
#  and you set your domain later under Settings -> Access & Domain
#  (HTTPS is then issued automatically).
# ================================================================
set -e

[[ $EUID -ne 0 ]] && echo "Run as root (use sudo)" && exit 1

apt update -y && apt upgrade -y
apt install -y git curl wget

rm -rf /tmp/mzeekobe
git clone https://github.com/justkeitany/Kobe_stores.git /tmp/mzeekobe

chmod +x /tmp/mzeekobe/install.sh
bash /tmp/mzeekobe/install.sh
