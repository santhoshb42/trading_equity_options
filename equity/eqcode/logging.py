"""
Logging Module - Equity Trading Bot

Handles three types of logs:
1. alerts.log - TradingView webhook alerts
2. statistics.log - All major steps and events
3. trades.csv - Trade entries and exits

All logs are organized in daily folders: logs/YYYY-MM-DD/
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
import csv
import os

# =============================================================================
# Configuration
# =============================================================================

# Base directory (equity bot root)
BASE_DIR = Path(__file__).parent.parent

# Logs directory
LOGS_DIR = BASE_DIR / "logs"

# Log levels
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# =============================================================================
# Daily Log Directory Management
# =============================================================================

def get_daily_log_dir() -> Path:
    """
    Get today's log directory (YYYY-MM-DD format) - matches AngelOne SDK.
    Creates the directory if it doesn't exist.
    
    Returns:
        Path to today's log directory
    """
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = LOGS_DIR / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    return daily_dir


def get_log_file(filename: str) -> Path:
    """
    Get full path to a log file in today's directory.
    
    Args:
        filename: Name of the log file (e.g., 'alerts.log')
    
    Returns:
        Full path to the log file
    """
    return get_daily_log_dir() / filename


# =============================================================================
# Logger Setup
# =============================================================================

def setup_logger(
    name: str,
    log_file: Optional[Path] = None,
    level: str = LOG_LEVEL,
    console: bool = True
) -> logging.Logger:
    """
    Setup a logger with file and optional console handlers.
    
    Args:
        name: Logger name
        log_file: Path to log file (optional)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console: Whether to also log to console
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()  # Clear existing handlers
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # File handler (if log_file provided)
    if log_file:
        # CRITICAL: RotatingFileHandler with explicit append mode to preserve logs across restarts
        file_handler = RotatingFileHandler(
            log_file,
            mode='a',  # CRITICAL: Explicit append mode to preserve logs
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


# =============================================================================
# Specialized Loggers
# =============================================================================

# Statistics Logger - All major steps and events
statistics_logger = setup_logger(
    "statistics",
    get_log_file("statistics.log"),
    console=True
)

# Alerts Logger - TradingView webhook alerts only
alerts_logger = setup_logger(
    "alerts",
    get_log_file("alerts.log"),
    console=False  # Don't spam console with alerts
)

# =============================================================================
# Logging Functions
# =============================================================================

def log_event(event_type: str, message: str, **kwargs):
    """
    Log a major event/step to statistics.log
    
    Args:
        event_type: Type of event (SESSION, ORDER, EXIT, ERROR, etc.)
        message: Event message
        **kwargs: Additional key-value pairs to log
    
    Example:
        log_event("SESSION", "Login successful", session_id="xyz123")
        → 2025-01-23 09:15:00 | INFO | SESSION | Login successful | session_id=xyz123
    """
    extra_info = " | ".join(f"{k}={v}" for k, v in kwargs.items())
    log_message = f"{event_type} | {message}"
    
    if extra_info:
        log_message += f" | {extra_info}"
    
    statistics_logger.info(log_message)


def log_alert(symbol: str, action: str, price: float = 0, **kwargs):
    """
    Log a TradingView alert to alerts.log
    
    Args:
        symbol: Stock symbol
        action: BUY or SELL
        price: Alert price
        **kwargs: Additional info (source, etc.)
    
    Example:
        log_alert("RELIANCE-EQ", "BUY", 2450.50, source="TradingView")
        → 2025-01-23 09:30:15 | INFO | ALERT | BUY | RELIANCE-EQ | price=2450.50 | source=TradingView
    """
    extra_info = " | ".join(f"{k}={v}" for k, v in kwargs.items())
    log_message = f"ALERT | {action} | {symbol}"
    
    if price > 0:
        log_message += f" | price={price:.2f}"
    
    if extra_info:
        log_message += f" | {extra_info}"
    
    alerts_logger.info(log_message)


def log_trade(
    action: str,
    symbol: str,
    quantity: int,
    entry_price: float = 0,
    exit_price: float = 0,
    capital_used: float = 0,
    sl_price: float = 0,
    pnl: float = 0,
    status: str = "OPEN",
    **kwargs
):
    """
    Log a trade to trades.csv
    
    Args:
        action: BUY or SELL
        symbol: Stock symbol
        quantity: Number of shares
        entry_price: Entry price (for BUY)
        exit_price: Exit price (for SELL)
        capital_used: Capital used for trade
        sl_price: Stop loss price
        pnl: Profit/Loss
        status: OPEN or CLOSED
        **kwargs: Additional fields
    
    CSV Format:
        date,time,symbol,action,quantity,entry_price,exit_price,capital_used,sl_price,pnl,status
    """
    csv_file = get_log_file("trades.csv")
    
    # Check if file exists to write header
    write_header = not csv_file.exists()
    
    # Prepare row data
    now = datetime.now()
    row = {
        'date': now.strftime('%Y-%m-%d'),  # Use YYYY-MM-DD to match directory format
        'time': now.strftime('%H:%M:%S'),
        'symbol': symbol,
        'action': action,
        'quantity': quantity,
        'entry_price': f"{entry_price:.2f}" if entry_price > 0 else "",
        'exit_price': f"{exit_price:.2f}" if exit_price > 0 else "",
        'capital_used': f"{capital_used:.2f}" if capital_used > 0 else "",
        'sl_price': f"{sl_price:.2f}" if sl_price > 0 else "",
        'pnl': f"{pnl:+.2f}" if pnl != 0 else "0.00",
        'status': status,
    }
    
    # Add extra fields
    row.update(kwargs)
    
    # Write to CSV
    try:
        with open(csv_file, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            
            if write_header:
                writer.writeheader()
            
            writer.writerow(row)
        
        # Also log to statistics for visibility
        log_event(
            "TRADE",
            f"{action} {quantity} {symbol}",
            entry=f"{entry_price:.2f}" if entry_price > 0 else "",
            exit=f"{exit_price:.2f}" if exit_price > 0 else "",
            pnl=f"{pnl:+.2f}" if pnl != 0 else "0.00",
            status=status
        )
        
    except Exception as e:
        statistics_logger.error(f"Failed to write to trades.csv: {e}")


# =============================================================================
# Convenience Functions
# =============================================================================

def info(message: str):
    """Quick info log to statistics."""
    statistics_logger.info(message)


def warning(message: str):
    """Quick warning log to statistics."""
    statistics_logger.warning(message)


def error(message: str, exc_info: bool = False):
    """Quick error log to statistics."""
    statistics_logger.error(message, exc_info=exc_info)


def debug(message: str):
    """Quick debug log to statistics."""
    statistics_logger.debug(message)


def critical(message: str):
    """Quick critical log to statistics."""
    statistics_logger.critical(message)


# =============================================================================
# Daily Summary Functions
# =============================================================================

def log_daily_summary(
    total_trades: int,
    winners: int,
    losers: int,
    total_pnl: float,
    capital_used: float,
    available_capital: float
):
    """
    Log daily trading summary.
    
    Args:
        total_trades: Total number of trades
        winners: Number of winning trades
        losers: Number of losing trades
        total_pnl: Total profit/loss
        capital_used: Total capital currently in use
        available_capital: Available capital
    """
    win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
    utilization = (capital_used / (capital_used + available_capital) * 100) if (capital_used + available_capital) > 0 else 0
    
    log_event(
        "DAILY_STATS",
        f"End of day summary",
        total_trades=total_trades,
        winners=winners,
        losers=losers,
        pnl=f"{total_pnl:+.2f}",
        win_rate=f"{win_rate:.1f}%"
    )
    
    log_event(
        "CAPITAL_USAGE",
        f"Capital snapshot",
        used=f"{capital_used:.2f}",
        available=f"{available_capital:.2f}",
        utilization=f"{utilization:.1f}%"
    )


# =============================================================================
# Initialization
# =============================================================================

def init_logging():
    """
    Initialize logging system.
    Creates necessary directories and logs startup message.
    """
    # Ensure logs directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create today's directory
    daily_dir = get_daily_log_dir()
    
    # Log initialization
    log_event("STARTUP", f"Logging initialized", log_dir=str(daily_dir))
    
    return True


# =============================================================================
# Auto-initialize on import
# =============================================================================

init_logging()


# =============================================================================
# Module Test
# =============================================================================

if __name__ == "__main__":
    print("Testing logging module...")
    print(f"Log directory: {get_daily_log_dir()}")
    print()
    
    # Test event logging
    print("1. Testing event logging...")
    log_event("TEST", "Testing event logging", test_param="value123")
    
    # Test alert logging
    print("2. Testing alert logging...")
    log_alert("RELIANCE-EQ", "BUY", 2450.50, source="TradingView")
    
    # Test trade logging
    print("3. Testing trade logging (BUY)...")
    log_trade(
        action="BUY",
        symbol="RELIANCE-EQ",
        quantity=10,
        entry_price=2450.50,
        capital_used=24505.00,
        sl_price=2401.49,
        status="OPEN"
    )
    
    print("4. Testing trade logging (SELL)...")
    log_trade(
        action="SELL",
        symbol="RELIANCE-EQ",
        quantity=10,
        entry_price=2450.50,
        exit_price=2480.20,
        capital_used=24505.00,
        sl_price=2401.49,
        pnl=297.00,
        status="CLOSED"
    )
    
    # Test daily summary
    print("5. Testing daily summary...")
    log_daily_summary(
        total_trades=15,
        winners=9,
        losers=6,
        total_pnl=5420.50,
        capital_used=80000,
        available_capital=20000
    )
    
    print()
    print("✓ All tests completed")
    print(f"✓ Check logs in: {get_daily_log_dir()}")
    print("  - alerts.log")
    print("  - statistics.log")
    print("  - trades.csv")