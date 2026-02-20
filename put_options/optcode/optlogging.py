"""
PUT Options Bot Logging Module

Comprehensive debug logging for PUT (PE) options trading operations.
Separate from CE Options bot - logs to put_options/ directory.
Logs all trades, positions, signals, errors with context.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .optconfig import BASE_DIR

# =============================================================================
# Logging Configuration
# =============================================================================

# Create logs directory
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Log file with timestamp
LOG_DATE = datetime.now().strftime("%Y-%m-%d")
LOG_DIR_DATED = LOGS_DIR / LOG_DATE
LOG_DIR_DATED.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR_DATED / "optbot.log"
EVENT_LOG_FILE = LOG_DIR_DATED / "events.jsonl"  # JSON Lines format for events
ERROR_LOG_FILE = LOG_DIR_DATED / "errors.log"
ALERT_LOG_FILE = LOG_DIR_DATED / "alerts.jsonl"
POSITION_LOG_FILE = LOG_DIR_DATED / "positions.jsonl"

# =============================================================================
# Logger Setup
# =============================================================================

class OptionsLogger:
    """Central logging for PUT options bot (PE trading)"""
    
    def __init__(self):
        self.logger = logging.getLogger('put_optbot')
        self.logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers
        self.logger.handlers.clear()
        
        # File handler - all logs
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)
        
        # Console handler - info and above
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        # Error handler
        error_handler = logging.FileHandler(ERROR_LOG_FILE)
        error_handler.setLevel(logging.ERROR)
        error_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        error_handler.setFormatter(error_formatter)
        self.logger.addHandler(error_handler)
    
    def debug(self, msg: str, **kwargs):
        """Debug level log"""
        self.logger.debug(f"{msg} | {self._format_kwargs(kwargs)}")
    
    def info(self, msg: str, **kwargs):
        """Info level log"""
        self.logger.info(f"{msg} | {self._format_kwargs(kwargs)}")
    
    def warning(self, msg: str, **kwargs):
        """Warning level log"""
        self.logger.warning(f"{msg} | {self._format_kwargs(kwargs)}")
    
    def error(self, msg: str, **kwargs):
        """Error level log"""
        self.logger.error(f"{msg} | {self._format_kwargs(kwargs)}")
    
    def critical(self, msg: str, **kwargs):
        """Critical level log"""
        self.logger.critical(f"{msg} | {self._format_kwargs(kwargs)}")
    
    @staticmethod
    def _format_kwargs(kwargs: Dict[str, Any]) -> str:
        """Format kwargs into readable string"""
        if not kwargs:
            return ""
        items = [f"{k}={v}" for k, v in kwargs.items()]
        return " | ".join(items)

# =============================================================================
# Event Logging (JSON Lines)
# =============================================================================

def log_event(event_type: str, message: str, **context) -> None:
    """Log event as JSON for structured analysis"""
    try:
        event = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'message': message,
            'context': context
        }
        
        with open(EVENT_LOG_FILE, 'a') as f:
            f.write(json.dumps(event) + '\n')
    except Exception as e:
        logger.error(f"Failed to log event: {str(e)}")

# =============================================================================
# Alert Logging
# =============================================================================

def log_alert(alert: Dict[str, Any], status: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Log webhook alert processing"""
    try:
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'alert': alert,
            'status': status,  # received, validated, rejected, accepted, executed
            'details': details or {}
        }
        
        with open(ALERT_LOG_FILE, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    except Exception as e:
        logger.error(f"Failed to log alert: {str(e)}")

# =============================================================================
# Position Logging
# =============================================================================

def log_position(action: str, position_data: Dict[str, Any]) -> None:
    """Log position changes"""
    try:
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,  # opened, updated, closed, expired
            'position': position_data
        }
        
        with open(POSITION_LOG_FILE, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    except Exception as e:
        logger.error(f"Failed to log position: {str(e)}")

# =============================================================================
# Global Logger Instance
# =============================================================================

logger = OptionsLogger()

# =============================================================================
# Convenience Functions
# =============================================================================

def log_broker_action(action: str, symbol: str, details: Dict[str, Any]) -> None:
    """Log broker API action"""
    logger.debug(f"BROKER_ACTION: {action} | {symbol}", **details)
    log_event("BROKER_ACTION", f"{action} {symbol}", **details)

def log_signal_validation(symbol: str, is_valid: bool, reason: str = "", **context) -> None:
    """Log signal validation result"""
    status = "VALID" if is_valid else "INVALID"
    logger.debug(f"SIGNAL_VALIDATION: {status} | {symbol} | {reason}", **context)
    log_event("SIGNAL_VALIDATION", f"{status}: {symbol}", reason=reason, **context)

def log_position_action(action: str, symbol: str, **details) -> None:
    """Log position action"""
    logger.info(f"POSITION_{action}: {symbol}", **details)
    log_event(f"POSITION_{action}", symbol, **details)

def log_pnl(symbol: str, pnl: float, pnl_percent: float, exit_reason: str, **details) -> None:
    """Log position P&L"""
    logger.info(f"PNL: {symbol} | ₹{pnl:.2f} ({pnl_percent:.2f}%) | {exit_reason}", **details)
    log_event("PNL", f"{symbol}: {pnl:.2f}", pnl=pnl, pnl_percent=pnl_percent, reason=exit_reason, **details)

def log_api_error(endpoint: str, error: str, **context) -> None:
    """Log API error"""
    logger.error(f"API_ERROR: {endpoint} | {error}", **context)
    log_event("API_ERROR", f"{endpoint}: {error}", **context)

def log_state(message: str, **state) -> None:
    """Log current state snapshot"""
    logger.debug(f"STATE: {message}", **state)
    log_event("STATE", message, **state)

# =============================================================================
# Log Summary Functions
# =============================================================================

def get_session_summary() -> Dict[str, Any]:
    """Get summary of current session from logs"""
    try:
        summary = {
            'alerts_received': 0,
            'alerts_accepted': 0,
            'alerts_rejected': 0,
            'positions_opened': 0,
            'positions_closed': 0,
            'total_pnl': 0.0,
            'winning_trades': 0,
            'losing_trades': 0,
            'errors': 0
        }
        
        # Count from alert log
        if ALERT_LOG_FILE.exists():
            with open(ALERT_LOG_FILE, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        summary['alerts_received'] += 1
                        if entry['status'] == 'accepted':
                            summary['alerts_accepted'] += 1
                        elif entry['status'] == 'rejected':
                            summary['alerts_rejected'] += 1
                    except:
                        pass
        
        # Count from position log
        if POSITION_LOG_FILE.exists():
            with open(POSITION_LOG_FILE, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry['action'] == 'opened':
                            summary['positions_opened'] += 1
                        elif entry['action'] == 'closed':
                            summary['positions_closed'] += 1
                            pos = entry.get('position', {})
                            pnl = pos.get('pnl', 0)
                            summary['total_pnl'] += pnl
                            if pnl > 0:
                                summary['winning_trades'] += 1
                            else:
                                summary['losing_trades'] += 1
                    except:
                        pass
        
        # Count errors
        if ERROR_LOG_FILE.exists():
            with open(ERROR_LOG_FILE, 'r') as f:
                summary['errors'] = len(f.readlines())
        
        return summary
    except Exception as e:
        logger.error(f"Failed to get session summary: {str(e)}")
        return {}

def print_session_summary() -> None:
    """Print formatted session summary"""
    summary = get_session_summary()
    
    print("\n" + "="*70)
    print("SESSION SUMMARY")
    print("="*70)
    print(f"Alerts Received: {summary.get('alerts_received', 0)}")
    print(f"  ✅ Accepted: {summary.get('alerts_accepted', 0)}")
    print(f"  ❌ Rejected: {summary.get('alerts_rejected', 0)}")
    print(f"\nPositions:")
    print(f"  Opened: {summary.get('positions_opened', 0)}")
    print(f"  Closed: {summary.get('positions_closed', 0)}")
    print(f"\nP&L:")
    print(f"  Total: ₹{summary.get('total_pnl', 0):.2f}")
    print(f"  Wins: {summary.get('winning_trades', 0)}")
    print(f"  Losses: {summary.get('losing_trades', 0)}")
    print(f"  Win Rate: {(summary.get('winning_trades', 0) / max(1, summary.get('positions_closed', 0)) * 100):.1f}%")
    print(f"\nErrors: {summary.get('errors', 0)}")
    print("="*70 + "\n")
