#!/usr/bin/env bash
# Remove the timer/service and restore /etc/hosts. Run with: sudo bash uninstall.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR=/etc/systemd/system
UNITS=(
    "github-hosts.service"
    "github-hosts.timer"
    "workbuddy-github-hosts.service"
    "workbuddy-github-hosts.timer"
)

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo bash $DIR/uninstall.sh"
    exit 1
fi

for unit in "${UNITS[@]}"; do
    systemctl disable --now "$unit" 2>/dev/null || true
    rm -f "$UNIT_DIR/$unit"
done
systemctl daemon-reload
python3 "$DIR/github-hosts.py" restore

echo "Rolled back. Hosts backups remain at /etc/hosts.github-hosts-backup-*"
echo "Older backups from the previous name are still at /etc/hosts.workbuddy-backup-*"
