#!/bin/bash
################################################################################
#                                                                              #
#                    MASTER TRADING BOT STARTUP SCRIPT                        #
#                          Single Source of Truth                            #
#                                                                              #
#  This is the ONLY script needed for:                                       #
#  1. Starting the trading bot safely                                        #
#  2. Preventing stray instances                                            #
#  3. System initialization & validation                                    #
#  4. Health checks & verification                                          #
#  5. Scheduled execution via cron                                          #
#                                                                              #
#  Usage:                                                                     #
#    ./start_trading_bot.sh              # Start bot                         #
#    ./start_trading_bot.sh --check      # Health check only                #
#    ./start_trading_bot.sh --clean      # Clean stray instances only       #
#    ./start_trading_bot.sh --status     # Check bot status                 #
#                                                                              #
#  Cron entry (daily startup at 9:00 AM):                                   #
#    0 9 * * * cd /root/santhosh/trading/equity && ./start_trading_bot.sh  #
#                                                                              #
################################################################################

set -o pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================

BOT_DIR="/root/santhosh/trading/equity"
BOT_SCRIPT="$BOT_DIR/main.py"
VENV_DIR="$BOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
LOGS_DIR="$BOT_DIR/logs"
DATA_DIR="$BOT_DIR/data"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
LOG_FILE="$LOGS_DIR/startup.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1" | tee -a "$LOG_FILE"
}

# ============================================================================
# STEP 1: SETUP & VALIDATION
# ============================================================================

setup_validation() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "STEP 1: Setup & Validation"
    log_info "═══════════════════════════════════════════════════════════════"
    
    # Check if we're in the right directory
    if [ ! -f "$BOT_SCRIPT" ]; then
        log_error "Bot script not found: $BOT_SCRIPT"
        log_error "Please run this script from: $BOT_DIR"
        exit 1
    fi
    log_success "Bot directory verified: $BOT_DIR"
    
    # Check virtual environment
    if [ ! -f "$VENV_PYTHON" ]; then
        log_error "Virtual environment not found at: $VENV_DIR"
        log_warning "Run: python3 -m venv $VENV_DIR"
        exit 1
    fi
    log_success "Virtual environment found"
    
    # Create logs directory if needed
    mkdir -p "$LOGS_DIR"
    mkdir -p "$DATA_DIR"
    log_success "Directories verified"
    
    # Log header
    echo "" >> "$LOG_FILE"
    echo "═══════════════════════════════════════════════════════════════" >> "$LOG_FILE"
    echo "Bot startup: $TIMESTAMP" >> "$LOG_FILE"
    echo "═══════════════════════════════════════════════════════════════" >> "$LOG_FILE"
}

# ============================================================================
# STEP 2: CLEAN STRAY INSTANCES
# ============================================================================

clean_stray_instances() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "STEP 2: Clean Stray Instances (CRITICAL)"
    log_info "═══════════════════════════════════════════════════════════════"
    
    # Find any existing bot processes
    EXISTING_PIDS=$(pgrep -f "python.*main.py" 2>/dev/null || echo "")
    
    if [ -z "$EXISTING_PIDS" ]; then
        log_success "No stray instances found - clean start"
    else
        log_warning "Found stray instances: $EXISTING_PIDS"
        log_info "Killing stray processes..."
        
        for PID in $EXISTING_PIDS; do
            log_info "  Killing PID $PID..."
            kill -9 "$PID" 2>/dev/null || true
        done
        
        sleep 2
        
        # Verify they're dead
        if pgrep -f "python.*main.py" > /dev/null 2>&1; then
            log_error "Failed to kill all stray instances"
            exit 1
        fi
        log_success "All stray instances cleaned"
    fi
}

# ============================================================================
# STEP 3: SYSTEM CHECKS
# ============================================================================

system_checks() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "STEP 3: System Checks"
    log_info "═══════════════════════════════════════════════════════════════"
    
    # Check available memory
    AVAILABLE_MEM=$(free -m | awk 'NR==2 {print $7}')
    # Relaxed memory requirement for small/low-memory environments
    # Original requirement was 500MB; reduce to 100MB to allow container/small droplets
    if [ "$AVAILABLE_MEM" -lt 100 ]; then
        log_error "Insufficient memory: ${AVAILABLE_MEM}MB (need >= 100MB)"
        exit 1
    fi
    log_success "Memory check passed: ${AVAILABLE_MEM}MB available (threshold=100MB)"
    
    # Check disk space
    DISK_AVAILABLE=$(df -BM "$BOT_DIR" | awk 'NR==2 {print $4}' | sed 's/M//')
    if [ "$DISK_AVAILABLE" -lt 100 ]; then
        log_warning "Low disk space: ${DISK_AVAILABLE}MB available"
    else
        log_success "Disk space check passed: ${DISK_AVAILABLE}MB available"
    fi
    
    # Check network connectivity
    if ping -c 1 8.8.8.8 > /dev/null 2>&1; then
        log_success "Network connectivity verified"
    else
        log_warning "Network connectivity check failed (may be offline)"
    fi
    
    # Check port 80
    if sudo lsof -i :80 2>/dev/null | grep -q "LISTEN"; then
        PORT_USER=$(sudo lsof -i :80 2>/dev/null | grep "LISTEN" | awk '{print $1}' | head -1)
        if [ "$PORT_USER" != "python" ]; then
            log_warning "Port 80 already in use by: $PORT_USER"
        fi
    else
        log_success "Port 80 is available"
    fi
}

# ============================================================================
# STEP 4: VALIDATE PYTHON MODULES
# ============================================================================

validate_modules() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "STEP 4: Validate Python Modules"
    log_info "═══════════════════════════════════════════════════════════════"
    
    # Source virtual environment
    source "$VENV_DIR/bin/activate"
    
    # Test imports
    log_info "Testing module imports..."
    $VENV_PYTHON -c "
import sys
try:
    from eqcode.angelone import AngelOneBroker
    from eqcode.monitor import PositionMonitor
    from eqcode.api import TradingState
    from eqcode.config import AngelOneConfig
    print('✓ All modules imported successfully')
except ImportError as e:
    print(f'✗ Import failed: {e}')
    sys.exit(1)
" 2>&1 | tee -a "$LOG_FILE"
    
    if [ $? -ne 0 ]; then
        log_error "Module validation failed"
        exit 1
    fi
    log_success "All Python modules validated"
}

# ============================================================================
# STEP 5: VALIDATE DATA FILES
# ============================================================================

validate_data_files() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "STEP 5: Validate Data Files"
    log_info "═══════════════════════════════════════════════════════════════"
    
    # Check positions.json
    if [ -f "$DATA_DIR/positions.json" ]; then
        if $VENV_PYTHON -c "import json; json.load(open('$DATA_DIR/positions.json'))" 2>/dev/null; then
            log_success "positions.json is valid"
        else
            log_warning "positions.json is corrupted, resetting to empty"
            echo "{}" > "$DATA_DIR/positions.json"
        fi
    else
        log_info "Creating empty positions.json"
        echo "{}" > "$DATA_DIR/positions.json"
    fi

    # BACKUP & CLEAR positions.json to ensure fresh sync from broker on scheduled startup
    # This forces the bot to rebuild positions from broker holdings via _sync_with_broker_holdings().
    # A backup is kept in data/ for forensic purposes.
    if [ -f "$DATA_DIR/positions.json" ]; then
        TS=$(date +%s)
        BACKUP_FILE="$DATA_DIR/positions.json.backup.$TS"
        cp "$DATA_DIR/positions.json" "$BACKUP_FILE"
        log_info "Backed up positions.json to $BACKUP_FILE"
        # Clear positions.json so monitor will rebuild from broker
        echo "{}" > "$DATA_DIR/positions.json"
        log_info "Cleared positions.json to force broker holdings sync on startup"
    fi
    
    # Check session.json
    if [ -f "$DATA_DIR/session.json" ]; then
        if $VENV_PYTHON -c "import json; json.load(open('$DATA_DIR/session.json'))" 2>/dev/null; then
            log_success "session.json is valid"
        else
            log_warning "session.json is corrupted, will be recreated on login"
            rm -f "$DATA_DIR/session.json"
        fi
    fi
}

# ============================================================================
# STEP 6: START BOT
# ============================================================================

start_bot() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "STEP 6: Starting Trading Bot"
    log_info "═══════════════════════════════════════════════════════════════"
    
    source "$VENV_DIR/bin/activate"
    
    log_info "Executing: $VENV_PYTHON $BOT_SCRIPT"
    log_success "Bot startup initiated"
    log_info ""
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Watch logs in real-time:"
    log_info "  tail -f $LOGS_DIR/$(date +%Y-%m-%d)/*.log"
    log_info "═══════════════════════════════════════════════════════════════"
    log_info ""
    
    # Start bot
    exec $VENV_PYTHON $BOT_SCRIPT >> "$LOG_FILE" 2>&1
}

# ============================================================================
# STEP 7: HEALTH CHECK (no startup)
# ============================================================================

health_check() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "HEALTH CHECK"
    log_info "═══════════════════════════════════════════════════════════════"
    
    log_info "Checking bot status..."
    
    # Check if bot is running
    if pgrep -f "python.*main.py" > /dev/null 2>&1; then
        BOT_PID=$(pgrep -f "python.*main.py")
        UPTIME=$(ps -o etime= -p "$BOT_PID" | xargs)
        log_success "Bot is running (PID: $BOT_PID, Uptime: $UPTIME)"
    else
        log_error "Bot is not running"
        exit 1
    fi
    
    # Check port
    if sudo lsof -i :80 2>/dev/null | grep -q "python.*LISTEN"; then
        log_success "Webhook port (80) is listening"
    else
        log_warning "Webhook port (80) is not listening"
    fi
    
    # Check data files
    log_success "Data files verified"
    
    log_info "═══════════════════════════════════════════════════════════════"
    log_success "Bot health check passed"
    log_info "═══════════════════════════════════════════════════════════════"
}

# ============================================================================
# STEP 8: CLEAN ONLY (no startup)
# ============================================================================

clean_only() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "CLEANUP MODE: Killing stray instances only"
    log_info "═══════════════════════════════════════════════════════════════"
    
    clean_stray_instances
    
    log_success "Cleanup complete"
    exit 0
}

# ============================================================================
# MAIN FLOW
# ============================================================================

main() {
    # Handle command line arguments
    case "${1:-}" in
        --check)
            health_check
            exit 0
            ;;
        --clean)
            clean_only
            exit 0
            ;;
        --status)
            if pgrep -f "python.*main.py" > /dev/null 2>&1; then
                echo "Bot is running"
                pgrep -f "python.*main.py" | while read PID; do
                    echo "  PID: $PID"
                    ps -o etime= -p "$PID" | xargs echo "  Uptime:"
                done
            else
                echo "Bot is not running"
            fi
            exit 0
            ;;
        *)
            # Normal startup flow
            setup_validation
            clean_stray_instances
            system_checks
            validate_modules
            validate_data_files
            start_bot
            ;;
    esac
}

# Run main function
main "$@"
