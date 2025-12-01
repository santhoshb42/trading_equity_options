#!/bin/bash

# Systemd Services Setup for Trading Bots and Webhook Router
# This script installs and enables all systemd services

echo "=========================================="
echo "🚀 Setting up Systemd Services"
echo "=========================================="
echo ""

# Backup existing services
BACKUP_DIR="/tmp/systemd_backup_$(date +%s)"
mkdir -p "$BACKUP_DIR"
echo "Backing up existing services to: $BACKUP_DIR"

cp /etc/systemd/system/webhook-router.service "$BACKUP_DIR/" 2>/dev/null || true
cp /etc/systemd/system/equity-trading-bot.service "$BACKUP_DIR/" 2>/dev/null || true
cp /etc/systemd/system/options-trading-bot.service "$BACKUP_DIR/" 2>/dev/null || true

echo ""
echo "=========================================="
echo "Installing Service Files"
echo "=========================================="
echo ""

# Copy service files
echo "📋 Installing webhook-router.service..."
sudo cp /root/santhosh/trading/deployment/webhook-router.service /etc/systemd/system/
echo "✅ Installed webhook-router.service"

echo ""
echo "📋 Installing equity-trading-bot.service..."
sudo cp /root/santhosh/trading/equity/deployment/equity-trading-bot.service /etc/systemd/system/
echo "✅ Installed equity-trading-bot.service"

echo ""
echo "📋 Installing options-trading-bot.service..."
sudo cp /root/santhosh/trading/options/deployment/options-trading-bot.service /etc/systemd/system/
echo "✅ Installed options-trading-bot.service"

echo ""
echo "=========================================="
echo "Reloading Systemd Daemon"
echo "=========================================="
echo ""

sudo systemctl daemon-reload
echo "✅ Systemd daemon reloaded"

echo ""
echo "=========================================="
echo "Enabling Services"
echo "=========================================="
echo ""

# Enable services (auto-start on boot)
echo "🔄 Enabling webhook-router..."
sudo systemctl enable webhook-router
echo "✅ webhook-router enabled"

echo ""
echo "🔄 Enabling equity-trading-bot..."
sudo systemctl enable equity-trading-bot
echo "✅ equity-trading-bot enabled"

echo ""
echo "🔄 Enabling options-trading-bot..."
sudo systemctl enable options-trading-bot
echo "✅ options-trading-bot enabled"

echo ""
echo "=========================================="
echo "Service Status"
echo "=========================================="
echo ""

echo "Webhook Router Status:"
sudo systemctl status webhook-router --no-pager || echo "Service not started yet (will start at 8:55 AM via cron)"

echo ""
echo "Equity Bot Status:"
sudo systemctl status equity-trading-bot --no-pager || echo "Service not started yet (will start at 9:05 AM via cron)"

echo ""
echo "Options Bot Status:"
sudo systemctl status options-trading-bot --no-pager || echo "Service not started yet (will start at 9:00 AM via cron)"

echo ""
echo "=========================================="
echo "Useful Commands"
echo "=========================================="
echo ""
echo "Check service status:"
echo "  sudo systemctl status webhook-router"
echo "  sudo systemctl status equity-trading-bot"
echo "  sudo systemctl status options-trading-bot"
echo ""
echo "Start services manually:"
echo "  sudo systemctl start webhook-router"
echo "  sudo systemctl start equity-trading-bot"
echo "  sudo systemctl start options-trading-bot"
echo ""
echo "Stop services:"
echo "  sudo systemctl stop webhook-router"
echo "  sudo systemctl stop equity-trading-bot"
echo "  sudo systemctl stop options-trading-bot"
echo ""
echo "View logs (live):"
echo "  sudo journalctl -u webhook-router -f"
echo "  sudo journalctl -u equity-trading-bot -f"
echo "  sudo journalctl -u options-trading-bot -f"
echo ""
echo "View logs (last 50 lines):"
echo "  sudo journalctl -u webhook-router -n 50"
echo "  sudo journalctl -u equity-trading-bot -n 50"
echo "  sudo journalctl -u options-trading-bot -n 50"
echo ""
echo "Disable auto-start on boot:"
echo "  sudo systemctl disable webhook-router"
echo "  sudo systemctl disable equity-trading-bot"
echo "  sudo systemctl disable options-trading-bot"
echo ""
echo "=========================================="
echo "IMPORTANT NOTES"
echo "=========================================="
echo ""
echo "1. Services are installed and enabled for auto-start on boot"
echo "2. Cron jobs (setup_cron.sh) will start them at scheduled times:"
echo "   - 08:55 AM: Webhook Router"
echo "   - 09:00 AM: Options Bot"
echo "   - 09:05 AM: Equity Bot"
echo ""
echo "3. Port Configuration:"
echo "   - Webhook Router: Port 80 (listens for TradingView alerts)"
echo "   - Equity Bot: Port 8080 (receives from router)"
echo "   - Options Bot: Port 8081 (receives from router)"
echo ""
echo "4. If you need to start services manually (testing):"
echo "   sudo systemctl start webhook-router"
echo "   sudo systemctl start equity-trading-bot"
echo "   sudo systemctl start options-trading-bot"
echo ""
echo "Backup location (if needed for restore): $BACKUP_DIR"
echo ""
echo "=========================================="
echo "✅ Systemd setup complete!"
echo "=========================================="

