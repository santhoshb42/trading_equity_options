#!/bin/bash
# Deploy pe-otm.service and pe-itm.service to systemd.
# Run once from any directory: bash PUT_OPTIONS/tools/setup_systemd.sh

set -e

BOT_DIR="/root/santhosh/trading/PUT_OPTIONS"
DEPLOY_DIR="$BOT_DIR/deployment"

if [ "$EUID" -ne 0 ]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

echo "=== PUT OPTIONS SYSTEMD SETUP ==="
echo "Bot dir: $BOT_DIR"
echo ""

for svc in pe-otm pe-itm; do
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

for svc in pe-otm pe-itm; do
    systemctl enable "${svc}.service"
    echo "Enabled ${svc}.service (auto-start on boot)"
done

echo ""
echo "=== SETUP COMPLETE ==="
echo ""
echo "Start both bots:"
echo "  systemctl start pe-otm.service pe-itm.service"
echo ""
echo "Check status:"
echo "  systemctl status pe-otm.service pe-itm.service"
echo ""
echo "Follow logs:"
echo "  journalctl -u pe-otm-bot -f"
echo "  journalctl -u pe-itm-bot -f"
