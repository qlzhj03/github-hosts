#!/usr/bin/env bash
# Install the GitHub hosts refresher: apply once, then re-check every 10 min.
# Run with: sudo bash install.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$DIR/github-hosts.py"
SERVICE=/etc/systemd/system/workbuddy-github-hosts.service
TIMER=/etc/systemd/system/workbuddy-github-hosts.timer

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo bash $DIR/install.sh"
    exit 1
fi

echo "==> Applying verified IPs now"
python3 "$SCRIPT" apply

echo "==> Installing systemd units"
cat > "$SERVICE" <<EOF
[Unit]
Description=Refresh verified GitHub IPs in /etc/hosts
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $SCRIPT apply
EOF

cat > "$TIMER" <<EOF
[Unit]
Description=Re-check GitHub IP health every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now workbuddy-github-hosts.timer

echo
echo "==> Done. Status:"
systemctl --no-pager status workbuddy-github-hosts.timer | head -5 || true
echo
echo "Useful commands:"
echo "  systemctl list-timers workbuddy-github-hosts.timer   # next refresh time"
echo "  journalctl -u workbuddy-github-hosts.service -n 30   # refresh logs"
echo "  sudo bash $DIR/uninstall.sh                          # full rollback"
