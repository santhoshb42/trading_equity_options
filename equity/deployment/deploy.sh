#!/bin/bash
# Deployment Manager for Indestructible Trading Bot

set -euo pipefail

BOT_DIR="/root/santhosh/trading/equity"
BACKUP_DIR="/var/backups/eqbot"
LOG_DIR="/var/log/eqbot"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/deployment.log"
}

# Deploy new version with zero downtime
deploy() {
    local version="${1:-$(date +%Y%m%d_%H%M%S)}"
    
    log "Starting deployment of version $version"
    
    # Create backup
    sudo mkdir -p "$BACKUP_DIR"
    tar -czf "$BACKUP_DIR/backup_$version.tar.gz" -C "$BOT_DIR" . --exclude='.venv' --exclude='logs' --exclude='.git'
    log "Backup created: backup_$version.tar.gz"
    
    # Stop watchdog temporarily
    sudo systemctl stop eqbot-watchdog || true
    
    # Update code (git pull or copy new files)
    cd "$BOT_DIR"
    
    # Install/update dependencies
    source .venv/bin/activate
    pip install -r requirements.txt
    
    # Restart service
    sudo systemctl restart eqbot
    
    # Wait for service to be ready
    sleep 10
    
    # Health check
    if curl -sf http://localhost:80/health > /dev/null; then
        log "Deployment successful - bot is healthy"
        sudo systemctl start eqbot-watchdog
    else
        log "Deployment failed - rolling back"
        rollback "$version"
        exit 1
    fi
}

# Rollback to previous version
rollback() {
    local backup_file="${1:-$(ls -t $BACKUP_DIR/backup_*.tar.gz | head -1)}"
    
    log "Rolling back to backup: $backup_file"
    
    sudo systemctl stop eqbot || true
    
    cd "$BOT_DIR"
    tar -xzf "$backup_file" .
    
    sudo systemctl start eqbot
    
    log "Rollback completed"
}

# Update system service
install_service() {
    log "Installing indestructible bot service..."
    
    sudo cp "$BOT_DIR/deployment/eqbot.service" /etc/systemd/system/
    sudo cp "$BOT_DIR/deployment/eqbot-watchdog.service" /etc/systemd/system/
    sudo cp "$BOT_DIR/deployment/eqbot-watchdog.timer" /etc/systemd/system/
    
    sudo systemctl daemon-reload
    sudo systemctl enable eqbot
    sudo systemctl enable eqbot-watchdog.timer
    
    log "Services installed and enabled"
}

case "${1:-help}" in
    deploy)
        deploy "${2:-}"
        ;;
    rollback)
        rollback "${2:-}"
        ;;
    install)
        install_service
        ;;
    *)
        echo "Usage: $0 {deploy|rollback|install} [version|backup_file]"
        exit 1
        ;;
esac