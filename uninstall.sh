#!/usr/bin/env bash
# Remove the timer/service and restore /etc/hosts. Run with: sudo bash uninstall.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo bash $DIR/uninstall.sh"
    exit 1
fi

systemctl disable --now workbuddy-github-hosts.timer 2>/dev/null || true
rm -f /etc/systemd/system/workbuddy-github-hosts.service
rm -f /etc/systemd/system/workbuddy-github-hosts.timer
systemctl daemon-reload
python3 "$DIR/github-hosts.py" restore

echo "Rolled back. /etc/hosts backups remain at /etc/hosts.workbuddy-backup-*"
