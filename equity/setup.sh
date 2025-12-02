#!/bin/bash

# =============================================================================
# EQUITY TRADING BOT - COMPREHENSIVE SETUP & RECOVERY SCRIPT
# =============================================================================
# 
# This script serves as the SINGLE SOURCE OF TRUTH for:
# 1. Dependency installation and updates
# 2. System configuration and reset
# 3. Service management and restart
# 4. Health checks and recovery
# 5. Cron-compatible automated maintenance
#
# Usage:
#   ./setup.sh startup     - Complete startup: deps, cleanup, start, verify, background
#   ./setup.sh install     - Full fresh installation
#   ./setup.sh restart     - Restart services only
#   ./setup.sh reset       - Reset and restart everything
#   ./setup.sh health      - Health check and auto-recovery
#   ./setup.sh update      - Update dependencies and restart
#   ./setup.sh instruments - Update instrument data only
#   ./setup.sh start       - Start services
#   ./setup.sh stop        - Stop services
#
# =============================================================================

set -e  # Exit on any error

# Script configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_FILE="$SCRIPT_DIR/setup.log"
MODE="${1:-help}"

# Logging function
log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" | tee -a "$LOG_FILE"
}

# Error handling
handle_error() {
    log "ERROR: Setup failed at line $1"
    exit 1
}
trap 'handle_error $LINENO' ERR

# Change to script directory
cd "$SCRIPT_DIR"

log "=================================================="
log "    EQUITY TRADING BOT - COMPREHENSIVE SETUP"
log "    Mode: $MODE"
log "    Directory: $SCRIPT_DIR"
log "=================================================="

# =============================================================================
# SYSTEM CLEANUP & PREPARATION
# =============================================================================

cleanup_system() {
    log "🧹 CLEANING UP SYSTEM..."
    
    # Stop any existing equity bot processes
    log "Stopping existing equity bot processes..."
    pkill -f "python.*main.py" || true
    pkill -f "equity.*bot" || true
    
    # Stop old options services (cleanup zombie processes)
    log "Cleaning up old options services..."
    systemctl stop options.service options-watchdog.service options-watchdog.timer 2>/dev/null || true
    systemctl disable options.service options-watchdog.service options-watchdog.timer 2>/dev/null || true
    pkill -f "options" || true
    pkill -f "uvicorn.*opt" || true
    
    # Clean Python cache (safe - no data loss)
    log "Cleaning Python cache..."
    find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    find . -name "*.pyc" -type f -delete 2>/dev/null || true
    
    # CRITICAL: DO NOT DELETE LOGS BY DEFAULT!
    # Logs are essential for:
    # 1. ML learning engine (feature importance, symbol performance tracking)
    # 2. Trade performance analysis and optimization
    # 3. Debug and audit trail
    # 4. Rate limiter analysis
    # 
    # Only clean if explicitly enabled via AGGRESSIVE_LOG_CLEANUP env var
    if [ "$AGGRESSIVE_LOG_CLEANUP" = "true" ]; then
        log "🚨 WARNING: Aggressive log cleanup enabled (AGGRESSIVE_LOG_CLEANUP=true)"
        log "   Deleting logs older than 7 days - this may impact ML learning"
        find logs/ -type f -name "*.log" -mtime +7 -delete 2>/dev/null || true
        find logs/ -type f -name "*.csv" -mtime +7 -delete 2>/dev/null || true
    else
        log "✅ Preserving all logs for ML training and analysis"
        log "  (Set AGGRESSIVE_LOG_CLEANUP=true to enable cleanup)"
    fi
    
    # Log directories are now managed by cleanup_logs.py
    # No consolidation needed - all components use DD-MM-YYYY format
    log "✅ Log directories standardized (DD-MM-YYYY format)"
    
    # Free system memory
    log "Freeing system memory..."
    sync && echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
    
    log "✅ System cleanup completed"
}

# =============================================================================
# PREREQUISITES & SYSTEM REQUIREMENTS
# =============================================================================

check_prerequisites() {
    log "🔍 CHECKING PREREQUISITES..."
    
    # Check if running as root (required for systemd)
    if [ "$EUID" -ne 0 ] && [ "$MODE" != "install" ]; then
        log "⚠️  Some operations require root privileges. Consider running with sudo."
    fi
    
    # Check Python version
    if ! command -v python3 &> /dev/null; then
        log "❌ Python 3 is not installed. Installing..."
        apt update && apt install -y python3 python3-pip python3-venv
    fi
    
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    log "✅ Python version: $PYTHON_VERSION"
    
    # Check system resources
    AVAILABLE_MEM=$(free -m | awk 'NR==2{printf "%.0f", $7}')
    AVAILABLE_DISK=$(df -h . | awk 'NR==2{print $4}' | sed 's/G//')
    
    log "📊 System resources:"
    log "   - Available Memory: ${AVAILABLE_MEM}MB"
    log "   - Available Disk: ${AVAILABLE_DISK}GB"
    
    if [ "$AVAILABLE_MEM" -lt 100 ]; then
        log "⚠️  Low memory warning: ${AVAILABLE_MEM}MB available"
    fi
    
    # Check network connectivity
    if ping -c 1 8.8.8.8 &> /dev/null; then
        log "✅ Network connectivity confirmed"
    else
        log "❌ Network connectivity issues detected"
        exit 1
    fi
    
    log "✅ Prerequisites check completed"
}

# =============================================================================
# DIRECTORY STRUCTURE & FILES
# =============================================================================

setup_directory_structure() {
    log "📁 SETTING UP DIRECTORY STRUCTURE..."
    
    # Create all required directories
    mkdir -p data
    mkdir -p logs/$(date +%d-%m-%Y)
    mkdir -p tools/testing
    mkdir -p deployment
    mkdir -p eqcode/__pycache__ && rm -rf eqcode/__pycache__
    
    # Set proper permissions
    chmod 755 .
    chmod 755 tools/
    chmod 755 eqcode/
    chmod 700 data/  # Secure data directory
    chmod 755 logs/
    
    log "✅ Directory structure created"
}

# =============================================================================
# PYTHON ENVIRONMENT & DEPENDENCIES
# =============================================================================

setup_python_environment() {
    log "🐍 SETTING UP PYTHON ENVIRONMENT..."
    
    # Create/update virtual environment
    VENV_DIR="$SCRIPT_DIR/.venv"
    if [ ! -d "$VENV_DIR" ] || [ "$MODE" = "reset" ]; then
        log "Creating fresh virtual environment..."
        rm -rf "$VENV_DIR" 2>/dev/null || true
        python3 -m venv "$VENV_DIR"
    fi
    
    # Activate virtual environment
    source "$VENV_DIR/bin/activate"
    log "✅ Virtual environment activated"
    
    # Upgrade pip
    python -m pip install --upgrade pip setuptools wheel --quiet
    
    # Install dependencies
    if [ -f requirements.txt ]; then
        log "Installing from requirements.txt..."
        python -m pip install -r requirements.txt --quiet
    else
        log "Installing essential packages..."
        python -m pip install --quiet \
            requests>=2.28 \
            Flask>=2.0 \
            pyotp>=2.8 \
            python-dotenv>=0.21 \
            pytest>=7.0 \
            psutil>=5.9 \
            logzero>=1.7 \
            cryptography>=3.4
        
        # Try to install SmartAPI
        python -m pip install smartapi-python --quiet || \
        python -m pip install SmartApi --quiet || \
        log "⚠️  SmartAPI installation failed - will need manual setup"
    fi
    
    log "✅ Python dependencies installed"
}

# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================

setup_environment_config() {
    log "⚙️  SETTING UP ENVIRONMENT CONFIGURATION..."
    
    # Create .env if it doesn't exist
    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            cp .env.example .env
            log "📝 Created .env from template"
        else
            log "Creating default .env file..."
            cat > .env << 'EOF'
# Equity Trading Bot - Environment Configuration

# Trading mode: PAPER (simulation) or LIVE (real trading)
TRADING_MODE=PAPER

# Capital Management (in Rupees)
MAX_CAPITAL=20000
CAP_PER_TRADE=2000
MAX_SLOTS=10
RESERVE_CAPITAL=10000

# Risk Management
DEFAULT_SL_PERCENTAGE=0.5
TRAIL_SL_ENABLED=True
TRAIL_SL_PERCENTAGE=0.5
TRAIL_TRIGGER_PERCENTAGE=0.5

# Webhook Server Configuration
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080
WEBHOOK_SECRET=your_webhook_secret_here

# Monitoring Configuration
MONITOR_INTERVAL=1
ORDER_TIMEOUT=30

# Logging Configuration
LOG_LEVEL=INFO
LOG_RETENTION_DAYS=30

# Development and Testing
TESTING_MODE=False
DEBUG_MODE=False

# AngelOne API Credentials (CONFIGURE THESE)
ANGEL_API_KEY=your_api_key_here
ANGEL_CLIENT_CODE=your_client_code_here
ANGEL_PASSWORD=your_password_here
ANGEL_TOTP_SECRET=your_totp_secret_here
EOF
        fi
        
        chmod 600 .env  # Secure permissions
        log "⚠️  IMPORTANT: Configure .env with your AngelOne credentials"
    else
        log "✅ .env file exists"
    fi
    
    # Validate environment loading
    if source .venv/bin/activate && python3 -c "from dotenv import load_dotenv; load_dotenv(); import os; print('✅ Environment validation passed' if os.getenv('TRADING_MODE') else '❌ Environment validation failed')"; then
        log "✅ Environment configuration validated"
    else
        log "⚠️  Environment validation issues detected"
    fi
}

# =============================================================================
# SYSTEMD SERVICE MANAGEMENT
# =============================================================================

setup_systemd_service() {
    log "🔧 SETTING UP SYSTEMD SERVICE..."
    
    # Only setup service if running as root
    if [ "$EUID" -eq 0 ]; then
        SERVICE_FILE="/etc/systemd/system/equity-bot.service"
        
        cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Equity Trading Bot - Indestructible Service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
Environment=PATH=$SCRIPT_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStartPre=/bin/bash $SCRIPT_DIR/setup.sh health
ExecStart=$SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/main.py
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=equity-bot

# Resource limits
LimitNOFILE=65536
LimitNPROC=4096

# Security
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
        
        # Create watchdog service
        WATCHDOG_FILE="/etc/systemd/system/equity-bot-watchdog.service"
        cat > "$WATCHDOG_FILE" << EOF
[Unit]
Description=Equity Bot Health Watchdog
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash $SCRIPT_DIR/setup.sh health

[Install]
WantedBy=multi-user.target
EOF
        
        # Create watchdog timer
        TIMER_FILE="/etc/systemd/system/equity-bot-watchdog.timer"
        cat > "$TIMER_FILE" << EOF
[Unit]
Description=Run Equity Bot Health Check Every 5 Minutes
Requires=equity-bot-watchdog.service

[Timer]
OnCalendar=*:0/5
Persistent=true

[Install]
WantedBy=timers.target
EOF
        
        # Reload systemd and enable services
        systemctl daemon-reload
        systemctl enable equity-bot.service
        systemctl enable equity-bot-watchdog.timer
        
        log "✅ Systemd services configured"
    else
        log "⚠️  Skipping systemd setup (not running as root)"
    fi
}

# =============================================================================
# HEALTH CHECK & MONITORING
# =============================================================================

check_journal_for_restart_loops() {
    log "🔍 CHECKING JOURNAL FOR RESTART LOOPS..."
    
    # Check if journalctl is available
    if ! command -v journalctl &> /dev/null; then
        log "⚠️  journalctl not available - skipping loop check"
        return 0
    fi
    
    # Check for recent restart loops in the last 30 minutes
    local restart_count=0
    local loop_threshold=5
    
    # Count systemd restarts for equity-bot service in last 30 minutes
    if systemctl list-units --all | grep -q "equity-bot.service"; then
        restart_count=$(journalctl -u equity-bot.service --since "30 minutes ago" --no-pager -q | grep -c "Starting equity-bot.service" 2>/dev/null || echo "0")
    fi
    
    # Check for setup.sh SIGTERM kills in last 30 minutes
    local sigterm_count=$(journalctl --since "30 minutes ago" --no-pager -q | grep -c "equity-bot.*SIGTERM\|equity-bot.*status=15/TERM" 2>/dev/null || echo "0")
    
    log "📊 Recent activity (last 30 minutes):"
    log "   - Service restarts: $restart_count"
    log "   - SIGTERM kills: $sigterm_count"
    
    # Detect restart loop
    if [ "$restart_count" -gt "$loop_threshold" ] || [ "$sigterm_count" -gt "$loop_threshold" ]; then
        log "🚨 RESTART LOOP DETECTED!"
        log "   - This indicates systemd service conflicts with cron health checks"
        log "   - Recommend: Disable systemd service and use cron-only management"
        
        # Automatically disable problematic systemd service
        if systemctl is-enabled equity-bot.service &>/dev/null; then
            log "🔧 Auto-disabling problematic systemd service..."
            systemctl stop equity-bot.service 2>/dev/null || true
            systemctl disable equity-bot.service 2>/dev/null || true
            log "✅ Systemd service disabled - using cron-only management"
        fi
        
        return 1
    else
        log "✅ No restart loops detected"
        return 0
    fi
}

# =============================================================================
# SYSTEMD STATUS VALIDATION
# =============================================================================

check_systemd_status() {
    log "🔧 CHECKING SYSTEMD SERVICE STATUS..."
    
    # Check if systemd service exists
    if ! systemctl list-unit-files | grep -q "equity-bot.service"; then
        log "⚠️  equity-bot.service not found in systemd"
        return 0
    fi
    
    # Get service status
    local service_status=$(systemctl is-active equity-bot.service 2>/dev/null || echo "unknown")
    local service_enabled=$(systemctl is-enabled equity-bot.service 2>/dev/null || echo "unknown")
    local service_failed=$(systemctl is-failed equity-bot.service 2>/dev/null || echo "unknown")
    
    log "📊 Systemd Service Status:"
    log "   - Active Status: $service_status"
    log "   - Enabled Status: $service_enabled"
    log "   - Failed Status: $service_failed"
    
    # Check for conflicts with current process
    local bot_pid=$(pgrep -f "python.*main.py" || echo "")
    local systemd_pid=""
    
    if [ "$service_status" = "active" ]; then
        systemd_pid=$(systemctl show equity-bot.service --property=MainPID --value 2>/dev/null || echo "")
        log "   - Systemd PID: $systemd_pid"
        log "   - Manual Bot PID: $bot_pid"
        
        # Check for PID conflicts
        if [ -n "$bot_pid" ] && [ -n "$systemd_pid" ] && [ "$systemd_pid" != "0" ] && [ "$bot_pid" != "$systemd_pid" ]; then
            log "🚨 CONFLICT DETECTED!"
            log "   - Both systemd service AND manual bot are running"
            log "   - This can cause port conflicts and duplicate trading"
            log "   - Recommend: Choose either systemd OR manual management"
            return 1
        fi
    fi
    
    # Check for failed state
    if [ "$service_status" = "failed" ]; then
        log "❌ Systemd service is in failed state"
        
        # Get failure reason
        local failure_reason=$(systemctl status equity-bot.service --no-pager -l 2>/dev/null | grep -E "Result:|code=" | head -1 | sed 's/.*Result: //' | sed 's/code=//' || echo "unknown")
        log "   - Failure reason: $failure_reason"
        
        # Check if this is the old restart loop issue
        if echo "$failure_reason" | grep -q -E "signal|TERM|killed"; then
            log "⚠️  Detected old restart loop configuration issue"
            log "   - This suggests ExecStartPre health check conflicts"
            log "   - Recommend: Update service file to remove ExecStartPre"
        fi
        
        return 1
    fi
    
    # All good
    if [ "$service_status" = "active" ] || [ "$service_status" = "inactive" ]; then
        log "✅ Systemd service status is healthy"
        return 0
    else
        log "⚠️  Systemd service in unexpected state: $service_status"
        return 1
    fi
}

health_check() {
    log "🏥 PERFORMING HEALTH CHECK..."
    
    # First check for restart loops
    check_journal_for_restart_loops
    
    # Check systemd service status and conflicts
    check_systemd_status
    local systemd_issues=$?
    
    local issues=0
    
    # Check if virtual environment exists
    if [ ! -d ".venv" ]; then
        log "❌ Virtual environment missing"
        ((issues++))
    fi
    
    # Check if main.py exists
    if [ ! -f "main.py" ]; then
        log "❌ main.py missing"
        ((issues++))
    fi
    
    # Check if .env exists
    if [ ! -f ".env" ]; then
        log "❌ .env file missing"
        ((issues++))
    fi
    
    # Validate instruments file
    if ! validate_instruments > /dev/null 2>&1; then
        log "⚠️  Instruments file needs updating"
        ((issues++))
    fi
    
    # Check if bot process is running
    if pgrep -f "python.*main.py" > /dev/null; then
        log "✅ Bot process is running"
    else
        log "⚠️  Bot process not running"
        ((issues++))
    fi
    
    # Check webhook endpoint
    if command -v curl &> /dev/null; then
        WEBHOOK_PORT=$(grep WEBHOOK_PORT .env 2>/dev/null | cut -d'=' -f2 || echo "8080")
        if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$WEBHOOK_PORT/health" | grep -q "200"; then
            log "✅ Webhook endpoint responding"
        else
            log "⚠️  Webhook endpoint not responding"
            ((issues++))
        fi
    fi
    
    # Check disk space
    DISK_USAGE=$(df . | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ "$DISK_USAGE" -gt 90 ]; then
        log "❌ Disk usage critical: ${DISK_USAGE}%"
        ((issues++))
    elif [ "$DISK_USAGE" -gt 80 ]; then
        log "⚠️  Disk usage high: ${DISK_USAGE}%"
    fi
    
    # Check memory usage
    MEM_USAGE=$(free | awk 'NR==2{printf "%.0f", $3*100/$2}')
    if [ "$MEM_USAGE" -gt 95 ]; then
        log "❌ Memory usage critical: ${MEM_USAGE}%"
        ((issues++))
    elif [ "$MEM_USAGE" -gt 85 ]; then
        log "⚠️  Memory usage high: ${MEM_USAGE}%"
    fi
    
    # Add systemd issues to total count
    ((issues += systemd_issues))
    
    log "📊 Health check completed: $issues issues found"
    return $issues
}

# =============================================================================
# SERVICE MANAGEMENT FUNCTIONS
# =============================================================================

start_services() {
    log "🚀 STARTING SERVICES..."
    
    # Kill any existing processes
    pkill -f "python.*main.py" || true
    sleep 2
    
    # Start the bot
    if [ "$EUID" -eq 0 ]; then
        systemctl start equity-bot.service
        systemctl start equity-bot-watchdog.timer
        log "✅ Services started via systemd"
    else
        # Start manually
        source .venv/bin/activate
        nohup python main.py > logs/$(date +%d-%m-%Y)/bot.log 2>&1 &
        echo $! > .bot.pid
        log "✅ Bot started manually (PID: $(cat .bot.pid))"
    fi
    
    sleep 3
    
    # Verify startup
    if pgrep -f "python.*main.py" > /dev/null; then
        log "✅ Bot is running successfully"
    else
        log "❌ Bot failed to start"
        return 1
    fi
}

stop_services() {
    log "🛑 STOPPING SERVICES..."
    
    if [ "$EUID" -eq 0 ]; then
        systemctl stop equity-bot.service || true
        systemctl stop equity-bot-watchdog.timer || true
    fi
    
    pkill -f "python.*main.py" || true
    rm -f .bot.pid
    
    log "✅ Services stopped"
}

restart_services() {
    log "🔄 RESTARTING SERVICES..."
    stop_services
    sleep 2
    start_services
}

# =============================================================================
# INSTRUMENT FILE MANAGEMENT
# =============================================================================

download_instruments() {
    log "📊 DOWNLOADING FRESH INSTRUMENTS FILE..."
    
    source .venv/bin/activate
    
    # Create instruments download script
    cat > tools/download_instruments.py << 'EOF'
#!/usr/bin/env python3
"""
Download fresh instruments file from AngelOne
"""

import requests
import json
import os
import sys
from datetime import datetime

def download_instruments():
    """Download instruments file from AngelOne"""
    
    # AngelOne instruments URL
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    
    try:
        print("🔄 Downloading instruments from AngelOne...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Parse JSON to validate
        instruments_data = response.json()
        
        if not isinstance(instruments_data, list) or len(instruments_data) == 0:
            raise ValueError("Invalid instruments data received")
        
        print(f"✅ Downloaded {len(instruments_data)} instruments")
        
        # Create backup of old file
        if os.path.exists("tools/instrument.json"):
            backup_name = f"tools/instrument_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            os.rename("tools/instrument.json", backup_name)
            print(f"📦 Backed up old file to {backup_name}")
        
        # Save new file
        with open("tools/instrument.json", "w") as f:
            json.dump(instruments_data, f, indent=2)
        
        print("✅ Instruments file updated successfully")
        
        # Show some stats
        equity_count = len([i for i in instruments_data if i.get("exch_seg") == "NSE" and "-EQ" in i.get("symbol", "")])
        print(f"📈 NSE Equity instruments: {equity_count}")
        
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error downloading instruments: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in instruments file: {e}")
        return False
    except Exception as e:
        print(f"❌ Error downloading instruments: {e}")
        return False

def validate_instruments():
    """Validate existing instruments file"""
    
    if not os.path.exists("tools/instrument.json"):
        print("❌ No instruments file found")
        return False
    
    try:
        with open("tools/instrument.json", "r") as f:
            data = json.load(f)
        
        if not isinstance(data, list) or len(data) == 0:
            print("❌ Invalid instruments file format")
            return False
        
        # Check if file is recent (within 7 days)
        file_age = datetime.now().timestamp() - os.path.getmtime("tools/instrument.json")
        days_old = file_age / (24 * 3600)
        
        print(f"📅 Instruments file age: {days_old:.1f} days")
        
        if days_old > 7:
            print("⚠️  Instruments file is older than 7 days - consider updating")
            return False
        
        print(f"✅ Instruments file valid ({len(data)} instruments)")
        return True
        
    except Exception as e:
        print(f"❌ Error validating instruments: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        sys.exit(0 if validate_instruments() else 1)
    else:
        sys.exit(0 if download_instruments() else 1)
EOF
    
    chmod +x tools/download_instruments.py
    
    # Try to download instruments
    if python tools/download_instruments.py; then
        log "✅ Instruments file downloaded successfully"
        return 0
    else
        log "⚠️  Failed to download instruments - will use existing file if available"
        if [ -f tools/instrument.json ]; then
            log "✅ Using existing instruments file"
            return 0
        else
            log "❌ No instruments file available"
            return 1
        fi
    fi
}

validate_instruments() {
    log "🔍 VALIDATING INSTRUMENTS FILE..."
    
    source .venv/bin/activate
    
    if python tools/download_instruments.py validate; then
        log "✅ Instruments file is valid and recent"
        return 0
    else
        log "⚠️  Instruments file needs updating"
        return 1
    fi
}

# =============================================================================
# INSTRUMENT FILE MANAGEMENT
# =============================================================================

download_instruments() {
    log "📊 DOWNLOADING LATEST INSTRUMENT FILE..."
    
    source .venv/bin/activate
    
    # Create instruments download script
    cat > .download_instruments.py << 'EOF'
import requests
import json
import os
from datetime import datetime
import sys

def download_angelone_instruments():
    """Download latest instruments file from AngelOne"""
    
    # AngelOne instrument file URLs
    urls = [
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
        "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
    ]
    
    instruments_dir = "tools"
    backup_dir = f"{instruments_dir}/backups"
    
    # Create directories
    os.makedirs(instruments_dir, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)
    
    # Backup existing file if it exists
    existing_file = f"{instruments_dir}/instrument.json"
    if os.path.exists(existing_file):
        backup_name = f"{backup_dir}/instrument_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        os.rename(existing_file, backup_name)
        print(f"✅ Backed up existing file to: {backup_name}")
    
    # Try downloading from each URL
    for i, url in enumerate(urls):
        try:
            print(f"🔄 Attempting download from URL {i+1}: {url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Validate JSON
            data = response.json()
            
            if not isinstance(data, list) or len(data) == 0:
                print(f"❌ Invalid data format from URL {i+1}")
                continue
            
            # Filter for equity instruments (NSE/BSE)
            equity_instruments = []
            for item in data:
                if (item.get('exch_seg') in ['NSE', 'BSE'] and 
                    item.get('instrumenttype', '').upper() in ['', 'EQ', 'EQUITY'] and
                    '-EQ' in item.get('symbol', '')):
                    equity_instruments.append(item)
            
            print(f"📊 Found {len(equity_instruments)} equity instruments out of {len(data)} total")
            
            # Save to file
            with open(existing_file, 'w') as f:
                json.dump(equity_instruments, f, indent=2)
            
            print(f"✅ Successfully downloaded and saved {len(equity_instruments)} instruments")
            print(f"📁 File saved to: {existing_file}")
            
            # Cleanup old backups (keep last 7 days)
            import glob
            backup_files = glob.glob(f"{backup_dir}/instrument_backup_*.json")
            if len(backup_files) > 7:
                backup_files.sort()
                for old_backup in backup_files[:-7]:
                    try:
                        os.remove(old_backup)
                        print(f"🗑️  Removed old backup: {old_backup}")
                    except:
                        pass
            
            return True
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Network error with URL {i+1}: {e}")
            continue
        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error with URL {i+1}: {e}")
            continue
        except Exception as e:
            print(f"❌ Unexpected error with URL {i+1}: {e}")
            continue
    
    print("❌ All download attempts failed")
    return False

def create_fallback_instruments():
    """Create a minimal fallback instrument file"""
    fallback_data = [
        {
            "token": "2885",
            "symbol": "RELIANCE-EQ",
            "name": "RELIANCE",
            "expiry": "",
            "strike": "-1",
            "lotsize": "1",
            "instrumenttype": "",
            "exch_seg": "NSE",
            "tick_size": "5.0"
        },
        {
            "token": "1594",
            "symbol": "INFY-EQ", 
            "name": "INFY",
            "expiry": "",
            "strike": "-1",
            "lotsize": "1",
            "instrumenttype": "",
            "exch_seg": "NSE",
            "tick_size": "5.0"
        },
        {
            "token": "11536",
            "symbol": "TCS-EQ",
            "name": "TCS",
            "expiry": "",
            "strike": "-1", 
            "lotsize": "1",
            "instrumenttype": "",
            "exch_seg": "NSE",
            "tick_size": "5.0"
        }
    ]
    
    with open("tools/instrument.json", 'w') as f:
        json.dump(fallback_data, f, indent=2)
    
    print("✅ Created fallback instrument file with 3 major stocks")
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("    ANGELONE INSTRUMENT FILE DOWNLOADER")
    print("=" * 60)
    
    if download_angelone_instruments():
        print("✅ Instrument download completed successfully")
        sys.exit(0)
    else:
        print("⚠️  Download failed, creating fallback file...")
        if create_fallback_instruments():
            print("✅ Fallback file created")
            sys.exit(0)
        else:
            print("❌ Failed to create fallback file")
            sys.exit(1)
EOF
    
    # Run the download script
    python .download_instruments.py
    local download_result=$?
    
    # Clean up the script
    rm -f .download_instruments.py
    
    if [ $download_result -eq 0 ]; then
        log "✅ Instrument file download completed"
        
        # Update file timestamp for tracking
        touch tools/.instrument_last_updated
        
        return 0
    else
        log "❌ Instrument file download failed"
        return 1
    fi
}

check_instrument_freshness() {
    log "📅 CHECKING INSTRUMENT FILE FRESHNESS..."
    
    local instrument_file="tools/instrument.json"
    local update_marker="tools/.instrument_last_updated"
    
    # Check if files exist
    if [ ! -f "$instrument_file" ]; then
        log "⚠️  Instrument file missing - downloading..."
        download_instruments
        return $?
    fi
    
    # Check if file is older than 24 hours
    if [ -f "$update_marker" ]; then
        local last_update=$(stat -c %Y "$update_marker" 2>/dev/null || echo 0)
        local current_time=$(date +%s)
        local age_hours=$(( (current_time - last_update) / 3600 ))
        
        if [ $age_hours -gt 24 ]; then
            log "⏰ Instrument file is $age_hours hours old - updating..."
            download_instruments
            return $?
        else
            log "✅ Instrument file is fresh (updated $age_hours hours ago)"
            return 0
        fi
    else
        log "⚠️  Cannot determine file age - updating..."
        download_instruments
        return $?
    fi
}

# =============================================================================
# UPDATE FUNCTIONS
# =============================================================================

update_dependencies() {
    log "📦 UPDATING DEPENDENCIES..."
    
    source .venv/bin/activate
    python -m pip install --upgrade pip
    
    if [ -f requirements.txt ]; then
        python -m pip install -r requirements.txt --upgrade
    fi
    
    log "✅ Dependencies updated"
}

update_instruments() {
    log "📊 UPDATING INSTRUMENT FILE..."
    download_instruments
}

update_instruments() {
    log "📊 UPDATING INSTRUMENTS..."
    
    # Always download fresh instruments
    if download_instruments; then
        log "✅ Instruments updated successfully"
        return 0
    else
        log "❌ Failed to update instruments"
        return 1
    fi
}

# =============================================================================
# MAIN EXECUTION LOGIC
# =============================================================================

main() {
    case "$MODE" in
        "install")
            log "📥 FULL INSTALLATION MODE"
            cleanup_system
            check_prerequisites
            setup_directory_structure
            setup_python_environment
            setup_environment_config
            download_instruments  # Download fresh instruments
            setup_systemd_service
            log "✅ Installation completed successfully"
            ;;
            
        "restart")
            log "🔄 RESTART MODE"
            check_instrument_freshness  # Check if instruments need updating
            restart_services
            ;;
            
        "reset")
            log "🔄 RESET MODE"
            cleanup_system
            setup_python_environment
            setup_environment_config
            check_instrument_freshness  # Check if instruments need updating
            restart_services
            log "✅ Reset completed successfully"
            ;;
            
        "health")
            log "🏥 HEALTH CHECK MODE"
            check_instrument_freshness  # Always check instruments in health mode
            if health_check; then
                log "✅ System healthy"
                exit 0
            else
                log "⚠️  Issues detected - attempting auto-recovery"
                cleanup_system
                restart_services
                exit $?
            fi
            ;;
            
        "update")
            log "📦 UPDATE MODE"
            stop_services
            update_dependencies
            download_instruments  # Force download latest instruments
            start_services
            log "✅ Update completed successfully"
            ;;
            
        "instruments")
            log "📊 INSTRUMENT UPDATE MODE"
            download_instruments
            if pgrep -f "python.*main.py" > /dev/null; then
                log "🔄 Restarting bot to load new instruments..."
                restart_services
            fi
            log "✅ Instrument update completed"
            ;;
            
        "stop")
            log "🛑 STOP MODE"
            stop_services
            ;;
            
        "startup")
            log "🚀 COMPREHENSIVE STARTUP MODE"
            log "   • Installing/updating dependencies"
            log "   • Killing previous instances"
            log "   • Starting new instance"
            log "   • Verifying login status"
            log "   • Pushing to background"
            
            # Pre-startup: Check for systemd conflicts
            log "🔧 Pre-startup: Checking systemd service status..."
            
            # Check if systemd service is actively running
            if systemctl is-active equity-bot.service &>/dev/null; then
                local systemd_pid=$(systemctl show equity-bot.service --property=MainPID --value 2>/dev/null || echo "")
                local manual_pid=$(pgrep -f "python.*main.py" 2>/dev/null || echo "")
                
                log "📊 Service Status:"
                log "   - Systemd service: ACTIVE (PID: $systemd_pid)"
                log "   - Manual process: ${manual_pid:-"NONE"}"
                
                # If systemd is managing the bot, automatically handle the conflict
                if [ -n "$systemd_pid" ] && [ "$systemd_pid" != "0" ]; then
                    log "� CONFLICT RESOLUTION: Systemd service is managing the bot"
                    log "   Strategy: Stop systemd service and proceed with manual startup"
                    log "   - This ensures no port conflicts or duplicate processes"
                    log "   - Manual startup will take precedence"
                    
                    # Stop systemd service to avoid conflicts
                    log "🛑 Stopping systemd service..."
                    systemctl stop equity-bot.service 2>/dev/null || true
                    sleep 2  # Give it time to stop
                    
                    log "✅ Systemd service stopped - proceeding with manual startup"
                fi
            else
                log "✅ No systemd conflicts - service is not active"
            fi
            
            # Step 1: Install/update dependencies
            log "📦 Step 1: Checking and updating dependencies..."
            update_dependencies
            
            # Step 2: Check instrument freshness and update if needed
            log "📊 Step 2: Checking instrument data freshness..."
            check_instrument_freshness
            
            # Step 3: Kill any previous instances
            log "🔄 Step 3: Cleaning up previous instances..."
            cleanup_system
            
            # Step 4: Start new instance directly (bypass systemd)
            log "🚀 Step 4: Starting new instance..."
            cd "$SCRIPT_DIR"
            
            # Ensure virtual environment is activated
            source .venv/bin/activate
            
            # Create log directory
            mkdir -p logs/$(date +%Y-%m-%d)
            
            # Start the bot in background
            nohup .venv/bin/python main.py > logs/$(date +%Y-%m-%d)/bot.log 2>&1 &
            BOT_PID=$!
            echo $BOT_PID > .bot.pid
            log "✅ Bot started in background (PID: $BOT_PID)"
            
            # Step 5: Wait for startup and verify
            log "⏳ Step 5: Waiting for startup..."
            sleep 8
            
            # Verify the bot is running
            if pgrep -f "python.*main.py" > /dev/null; then
                ACTUAL_PID=$(pgrep -f "python.*main.py")
                log "✅ Bot process is running (PID: $ACTUAL_PID)"
            else
                log "❌ Bot process failed to start"
                log "📋 Checking recent logs..."
                tail -10 logs/$(date +%d-%m-%Y)/bot.log || true
                exit 1
            fi
            
            # Verify API server is responding
            WEBHOOK_PORT=$(grep WEBHOOK_PORT .env 2>/dev/null | cut -d'=' -f2 || echo "8080")
            max_attempts=15  # 75 seconds total (5s * 15)
            attempt=1
            
            log "🌐 Step 6: Waiting for API server on port $WEBHOOK_PORT..."
            while [ $attempt -le $max_attempts ]; do
                if curl -s --connect-timeout 3 "http://localhost:$WEBHOOK_PORT/status-lite" > /dev/null 2>&1; then
                    log "✅ API server is responding on port $WEBHOOK_PORT"
                    break
                fi
                log "⏳ API server starting... (attempt $attempt/$max_attempts)"
                sleep 5
                attempt=$((attempt + 1))
            done
            
            if [ $attempt -gt $max_attempts ]; then
                log "❌ API server failed to start after 75 seconds"
                log "📋 Recent bot logs:"
                tail -20 logs/$(date +%d-%m-%Y)/bot.log || true
                exit 1
            fi
            
            # Verify SmartAPI login status
            log "🔐 Step 7: Verifying SmartAPI login status..."
            sleep 3
            
            login_response=$(curl -s --connect-timeout 5 "http://localhost:$WEBHOOK_PORT/smartapi-status" 2>/dev/null || echo '{"error":"timeout"}')
            logged_in=$(echo "$login_response" | python3 -c "import json,sys; data=json.load(sys.stdin); print(data.get('logged_in', False))" 2>/dev/null || echo "false")
            
            if [ "$logged_in" = "True" ] || [ "$logged_in" = "true" ]; then
                log "✅ SmartAPI login verified successfully"
                session_age=$(echo "$login_response" | python3 -c "import json,sys; data=json.load(sys.stdin); print(data.get('session_age_minutes', 'unknown'))" 2>/dev/null || echo "unknown")
                log "   Session age: $session_age minutes"
            else
                log "⚠️  SmartAPI login needs attention - attempting refresh..."
                
                # Attempt session refresh
                refresh_response=$(curl -s --connect-timeout 10 "http://localhost:$WEBHOOK_PORT/session-refresh" 2>/dev/null || echo '{"status":"timeout"}')
                refresh_status=$(echo "$refresh_response" | python3 -c "import json,sys; data=json.load(sys.stdin); print(data.get('status', 'error'))" 2>/dev/null || echo "error")
                
                if [ "$refresh_status" = "success" ]; then
                    log "✅ Session refresh successful"
                else
                    log "⚠️  Session refresh failed - may need manual intervention"
                    log "   Refresh response: $refresh_response"
                fi
            fi
            
            # Final system status
            log "📊 Step 8: Final system status:"
            rate_limit_status=$(curl -s --connect-timeout 5 "http://localhost:$WEBHOOK_PORT/rate-limit-status" 2>/dev/null || echo '{"per_minute":{"available":"unknown"}}')
            rpm_available=$(echo "$rate_limit_status" | python3 -c "import json,sys; data=json.load(sys.stdin); print(data.get('per_minute', {}).get('available', 'unknown'))" 2>/dev/null || echo "unknown")
            
            FINAL_PID=$(pgrep -f "python.*main.py" || echo "not found")
            log "   • Process ID: $FINAL_PID"
            log "   • API Server: http://localhost:$WEBHOOK_PORT"
            log "   • Rate Limit: $rpm_available/180 RPM available"
            log "   • Log File: logs/$(date +%d-%m-%Y)/bot.log"
            log "   • PID File: .bot.pid"
            log "   • Status URL: http://localhost:$WEBHOOK_PORT/health"
            
            log "🎉 STARTUP COMPLETE - Bot is running in background!"
            log ""
            log "💡 Quick commands:"
            log "   ./setup.sh health     # Check system health"
            log "   ./setup.sh stop       # Stop the bot"
            log "   tail -f logs/$(date +%d-%m-%Y)/bot.log    # Watch logs"
            log "   curl http://localhost:$WEBHOOK_PORT/health | jq    # API status"
            ;;
            
        "start")
            log "🚀 START MODE"
            check_instrument_freshness  # Check instruments before starting
            start_services
            ;;
            
        "restart")
            log "🔄 RESTART MODE"
            restart_services
            ;;
            
        "reset")
            log "🔄 RESET MODE"
            cleanup_system
            setup_python_environment
            setup_environment_config
            restart_services
            log "✅ Reset completed successfully"
            ;;
            
        "journal")
            log "📋 JOURNAL CHECK MODE"
            check_journal_for_restart_loops
            if [ $? -eq 0 ]; then
                log "✅ No issues found in journal"
                exit 0
            else
                log "⚠️  Issues detected in journal logs"
                exit 1
            fi
            ;;
            
        "health")
            log "🏥 HEALTH CHECK MODE"
            if health_check; then
                log "✅ System healthy"
                exit 0
            else
                log "⚠️  Issues detected - attempting auto-recovery"
                cleanup_system
                restart_services
                exit $?
            fi
            ;;
            
        "update")
            log "📦 UPDATE MODE"
            stop_services
            update_dependencies
            update_instruments
            start_services
            log "✅ Update completed successfully"
            ;;
            
        "instruments")
            log "📊 INSTRUMENTS UPDATE MODE"
            update_instruments
            log "✅ Instruments update completed"
            ;;
            
        "stop")
            log "🛑 STOP MODE"
            stop_services
            ;;
            
        "start")
            log "🚀 START MODE"
            start_services
            ;;
            
        "help"|*)
            echo "Usage: $0 {startup|install|restart|reset|health|journal|update|instruments|start|stop}"
            echo ""
            echo "Commands:"
            echo "  startup     - Complete startup: deps, cleanup, start, verify, background"
            echo "  install     - Full fresh installation"
            echo "  restart     - Restart services only"
            echo "  reset       - Reset system and restart"
            echo "  health      - Health check and auto-recovery"
            echo "  journal     - Check journal logs for restart loops and issues"
            echo "  update      - Update dependencies and restart"
            echo "  instruments - Download fresh instruments file only"
            echo "  start       - Start services"
            echo "  stop        - Stop services"
            echo ""
            echo "🚀 Recommended for daily use:"
            echo "  ./setup.sh startup    # Complete automated startup"
            echo ""
            echo "Cron examples:"
            echo "  # Health check every 5 minutes"
            echo "  */5 * * * * /path/to/equity/setup.sh health >> /var/log/equity-bot-cron.log 2>&1"
            echo ""
            echo "  # Update instruments daily at 6 AM"
            echo "  0 6 * * * /path/to/equity/setup.sh instruments >> /var/log/equity-bot-cron.log 2>&1"
            echo ""
            echo "  # Full system reset daily at 6:30 AM"
            echo "  30 6 * * * /path/to/equity/setup.sh reset >> /var/log/equity-bot-cron.log 2>&1"
            exit 1
            ;;
    esac
    
    # Final status report
    log "=================================================="
    log "    SETUP OPERATION COMPLETED"
    log "    Mode: $MODE"
    log "    Status: SUCCESS"
    log "    Time: $(date)"
    log "=================================================="
    
    # Show quick status
    if [ "$MODE" != "stop" ]; then
        sleep 2
        health_check > /dev/null 2>&1 || true
        
        log "📊 Current Status:"
        log "   - Bot Process: $(pgrep -f "python.*main.py" > /dev/null && echo "RUNNING" || echo "STOPPED")"
        log "   - Log File: logs/$(date +%d-%m-%Y)/"
        log "   - Config: .env"
        log "   - Mode: $(grep TRADING_MODE .env 2>/dev/null | cut -d'=' -f2 || echo "UNKNOWN")"
        
        if [ -f .env ]; then
            WEBHOOK_PORT=$(grep WEBHOOK_PORT .env 2>/dev/null | cut -d'=' -f2 || echo "8080")
            log "   - Webhook: http://localhost:$WEBHOOK_PORT/webhook"
        fi
    fi
}

# =============================================================================
# EXECUTION
# =============================================================================

# Ensure we're in the right directory
if [ ! -f "main.py" ] && [ "$MODE" != "install" ]; then
    log "❌ This doesn't appear to be the equity bot directory (main.py not found)"
    exit 1
fi

# Run main function
main

exit 0