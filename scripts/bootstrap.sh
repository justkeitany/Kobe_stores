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

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; DIM=$'\033[2m'; NC=$'\033[0m'; CLR=$'\033[K'
LOG="/var/log/iptv-install.log"

[[ $EUID -ne 0 ]] && { echo -e "${RED}Run as root (use sudo)${NC}"; exit 1; }
: > "$LOG"

# Quiet step runner with a spinner — same clean style as install.sh, so the
# bootstrap apt/clone noise goes to the log instead of the screen.
run() {
    local desc="$1"; shift
    ( "$@" ) >>"$LOG" 2>&1 &
    local pid=$! frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' n=10 i=0
    while kill -0 "$pid" 2>/dev/null; do
        printf '\r  %s%s%s %s' "$CYAN" "${frames:i++%n:1}" "$NC" "$desc"; sleep 0.1
    done
    if wait "$pid"; then printf '\r  %s✓%s %s%s\n' "$GREEN" "$NC" "$desc" "$CLR"
    else printf '\r  %s✗%s %s%s\n' "$RED" "$NC" "$desc" "$CLR"; tail -n 20 "$LOG" | sed 's/^/    /'; exit 1; fi
}

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1

echo -e "\n${DIM}Preparing the IPTV Panel installer…${NC}\n"

prep_apt()   { apt-get update -y; apt-get upgrade -y -o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef; }
prep_tools() { apt-get install -y git curl wget; }
prep_clone() { rm -rf /tmp/mzeekobe; git clone --depth 1 https://github.com/justkeitany/Kobe_stores.git /tmp/mzeekobe; }

run "Preparing system packages" prep_apt
run "Installing git"            prep_tools
run "Fetching IPTV Panel"       prep_clone

chmod +x /tmp/mzeekobe/install.sh
exec bash /tmp/mzeekobe/install.sh
