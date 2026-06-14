#!/bin/bash
# Deploy ce-otm.service and ce-itm.service to systemd.
# Run once from any directory: bash CE_OPTIONS/tools/setup_systemd.sh

set -e

BOT_DIR="/root/santhosh/trading/CE_OPTIONS"
DEPLOY_DIR="$BOT_DIR/deployment"

if [ "$EUID" -ne 0 ]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

echo "=== CE OPTIONS SYSTEMD SETUP ==="
echo "Bot dir: $BOT_DIR"
echo ""

for svc in ce-otm ce-itm; do
    SRC="$DEPLOY_DIR/${svc}.service"
    DST="/etc/systemd/system/${svc}.service"

    if [ ! -f "$SRC" ]; then
        echo "ERROR: $SRC not found"
        exit 1
    fi

    [ -f "$DST" ] && cp "$DST" "${DST}.bak.$(date +%s)" && echo "Backed up existing $DST"
    install -m 0644 "$SRC" "$DST"
    echo "Installed $DST"
done

chmod 0755 "$BOT_DIR/tools/optbot-watchdog.sh"

systemctl daemon-reload
echo ""

for svc in ce-otm ce-itm; do
    systemctl enable "${svc}.service"
    echo "Enabled ${svc}.service (auto-start on boot)"
done

echo ""
echo "=== SETUP COMPLETE ==="
echo ""
echo "Start both bots:"
echo "  systemctl start ce-otm.service ce-itm.service"
echo ""
echo "Check status:"
echo "  systemctl status ce-otm.service ce-itm.service"
echo ""
echo "Follow logs:"
echo "  journalctl -u ce-otm-bot -f"
echo "  journalctl -u ce-itm-bot -f"
