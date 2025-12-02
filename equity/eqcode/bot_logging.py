"""
Comprehensive logging system for autonomous trading bot operation.

Critical for debugging when running unattended. Every major operation is logged
with enough detail for post-session analysis and issue resolution.

Provides: log_event, log_alert, log_trade, log_webhook, log_order, log_monitor, log_error
"""
import logging
import os
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import LoggingConfig, BASE_DIR


# Global to track current log directory date
_current_log_date = None
_log_dir_cache = None


def _get_log_dir() -> Path:
    """Get log directory for current date, creates new folder at midnight"""
    global _current_log_date, _log_dir_cache
    
    # Use YYYY-MM-DD format (consistent across all modules)
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Check if we need to create a new directory (day changed)
    if _current_log_date != current_date:
        _current_log_date = current_date
        _log_dir_cache = BASE_DIR / "logs" / current_date
        _log_dir_cache.mkdir(parents=True, exist_ok=True)
        
        # Log the rollover if it's not the first time
        if _current_log_date is not None:
            print(f"📅 Log directory rolled over to: {_log_dir_cache}")
    
    return _log_dir_cache


# Initialize log directory (for backward compatibility only - don't use static file paths!)
# Functions should call _get_log_dir() directly to ensure midnight rollover
LOG_DIR = _get_log_dir()
LOG_FILE = LOG_DIR / "bot.log"
DETAILED_LOG_FILE = LOG_DIR / "detailed.log"
ERROR_LOG_FILE = LOG_DIR / "errors.log"


def _get_logger(name: str = "equity_bot", log_file: Path = None):
    """Get or create logger with both file and console handlers"""
    # Use current log directory
    if log_file is None:
        log_file = _get_log_dir() / "bot.log"
    
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(getattr(logging, LoggingConfig.LOG_LEVEL, logging.INFO))
        
        # Enhanced formatter with more context
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(getattr(logging, LoggingConfig.LOG_LEVEL, logging.INFO))
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # File handler - CRITICAL: Use explicit append mode ("a") to preserve logs across restarts
        # DEFAULT FileHandler behavior should be append, but explicitly specify to be sure
        fh = logging.FileHandler(str(log_file), mode='a')  # CRITICAL: Explicit append mode
        fh.setLevel(logging.DEBUG)  # Always DEBUG level for files
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def _format_data(data: Dict[str, Any]) -> str:
    """Format data dictionary for logging with JSON fallback"""
    try:
        if not data:
            return ""
        
        # Create readable key=value format
        formatted_items = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                formatted_items.append(f"{k}={json.dumps(v)}")
            else:
                formatted_items.append(f"{k}={v}")
        
        return " | " + " | ".join(formatted_items)
    except Exception:
        # Fallback to string representation
        return f" | data={str(data)}"


def log_event(event_type: str, message: str, **data: Any) -> None:
    """Enhanced event logging with structured data"""
    logger = _get_logger()
    formatted_data = _format_data(data)
    logger.info(f"{event_type} | {message}{formatted_data}")


def log_alert(symbol: str, action: str, price: float, source: str = "TradingView", 
              validation_status: str = "ACCEPTED", rejection_reason: str = None,
              confidence: float = None, score: float = None) -> None:
    """
    Alert reception logging - EVERY incoming alert logged to CSV
    This captures both accepted and rejected alerts for complete audit trail
    """
    logger = _get_logger()
    logger.info(f"ALERT_RECEIVED | {source} | {action} {symbol} @ ₹{price:.2f} | Status: {validation_status}")

    # Write to all_alerts.csv for EVERY alert (accepted + rejected)
    log_dir = _get_log_dir()
    all_alerts_csv = log_dir / "all_alerts.csv"
    
    # Create header if file doesn't exist
    if not all_alerts_csv.exists():
        with open(all_alerts_csv, "w") as f:
            f.write("timestamp,source,symbol,action,price,confidence,score,validation_status,rejection_reason\n")
    
    # Append alert data
    with open(all_alerts_csv, "a") as f:
        timestamp = datetime.now().isoformat()
        confidence_str = f"{confidence:.2f}" if confidence is not None else ""
        score_str = f"{score:.2f}" if score is not None else ""
        rejection_reason_str = rejection_reason or ""
        
        f.write(f"{timestamp},{source},{symbol},{action},{price:.2f},{confidence_str},{score_str},{validation_status},{rejection_reason_str}\n")
    
    # Also write to alerts.log for backward compatibility
    alerts_log_file = log_dir / "alerts.log"
    with open(alerts_log_file, "a") as f:
        status_suffix = f" [{validation_status}]" if validation_status != "ACCEPTED" else ""
        f.write(f"{datetime.now().isoformat()} | {source} | {action} {symbol} @ ₹{price:.2f}{status_suffix}\n")


def log_trade_execution(stage: str, symbol: str, action: str, **details) -> None:
    """
    Log comprehensive trade execution pipeline for debugging missed trades
    Stages: ALERT_RECEIVED, VALIDATION_PASSED, CAPITAL_CHECK, ORDER_PLACED, 
            ORDER_CONFIRMED, POSITION_ADDED, EXECUTION_COMPLETE, EXECUTION_FAILED
    """
    log_dir = _get_log_dir()
    logger = _get_logger("trade_execution", log_dir / "detailed.log")
    
    # Create execution log CSV for detailed tracking
    execution_csv = log_dir / "trade_execution.csv"
    
    if not execution_csv.exists():
        with open(execution_csv, "w") as f:
            f.write("timestamp,stage,symbol,action,details\n")
    
    with open(execution_csv, "a") as f:
        timestamp = datetime.now().isoformat()
        details_str = json.dumps(details) if details else ""
        f.write(f"{timestamp},{stage},{symbol},{action},{details_str}\n")
    
    logger.info(f"TRADE_EXECUTION | {stage} | {symbol} | {action}{_format_data(details)}")


def log_broker_error(error_type: str, error_code: str = None, message: str = None,
                    endpoint: str = None, context: Dict[str, Any] = None,
                    recovery_attempted: bool = False, recovery_success: bool = False) -> None:
    """
    Log broker-related errors (session, API, rate limit, crashes)
    Critical for diagnosing missed trades due to broker issues
    
    Error types: SESSION_EXPIRED, SESSION_REFRESH_FAILED, API_ERROR, 
                 RATE_LIMIT_EXCEEDED, LOGIN_FAILED, TOKEN_INVALID, 
                 ORDER_API_FAILED, BROKER_CRASH
    """
    log_dir = _get_log_dir()
    logger = _get_logger("broker_errors", log_dir / "errors.log")
    
    # Create broker_errors.csv for detailed tracking
    broker_errors_csv = log_dir / "broker_errors.csv"
    
    if not broker_errors_csv.exists():
        with open(broker_errors_csv, "w") as f:
            f.write("timestamp,error_type,error_code,endpoint,message,context,recovery_attempted,recovery_success\n")
    
    with open(broker_errors_csv, "a") as f:
        timestamp = datetime.now().isoformat()
        error_code_str = error_code or ""
        endpoint_str = endpoint or ""
        message_str = (message or "").replace(",", ";")  # Escape commas for CSV
        context_str = json.dumps(context) if context else ""
        
        f.write(f"{timestamp},{error_type},{error_code_str},{endpoint_str},{message_str},{context_str},{recovery_attempted},{recovery_success}\n")
    
    # Log to main log with full details
    log_msg = f"BROKER_ERROR | {error_type}"
    if error_code:
        log_msg += f" | Code: {error_code}"
    if endpoint:
        log_msg += f" | Endpoint: {endpoint}"
    if message:
        log_msg += f" | {message}"
    if recovery_attempted:
        recovery_status = "SUCCESS" if recovery_success else "FAILED"
        log_msg += f" | Recovery: {recovery_status}"
    
    logger.error(log_msg + _format_data(context or {}))


def log_webhook(status: str, alert_data: Dict[str, Any] = None, 
                processing_time_ms: float = None, response_code: int = None,
                error_details: str = None, symbol: str = None, action: str = None,
                price: float = None) -> None:
    """Comprehensive webhook processing logging"""
    log_dir = _get_log_dir()
    logger = _get_logger("webhook", log_dir / "detailed.log")
    
    # Handle both dict format and individual parameters
    if alert_data:
        symbol = alert_data.get('symbol', symbol)
        action = alert_data.get('action', action)
        price = alert_data.get('price', price)
    
    log_data = {
        'status': status,
        'symbol': symbol or 'UNKNOWN',
        'action': action or 'UNKNOWN',
        'price': price or 0,
        'price': price,
        'timestamp': datetime.now().isoformat(),
        'raw_data': json.dumps(alert_data) if len(str(alert_data)) < 500 else str(alert_data)[:500] + "..."
    }
    
    if processing_time_ms:
        log_data['processing_time_ms'] = processing_time_ms
    if response_code:
        log_data['response_code'] = response_code
    if error_details:
        log_data['error'] = error_details
    
    logger.info(f"WEBHOOK_{status.upper()}{_format_data(log_data)}")


def log_order(stage: str, symbol: str, action: str, quantity: int = None, 
              price: float = None, order_id: str = None, 
              api_response: Dict = None, error: str = None) -> None:
    """Detailed order lifecycle logging"""
    log_dir = _get_log_dir()
    logger = _get_logger("orders", log_dir / "detailed.log")
    
    log_data = {
        'stage': stage,  # PLACING, PLACED, CONFIRMED, FAILED, CANCELLED
        'symbol': symbol,
        'action': action,
        'timestamp': datetime.now().isoformat()
    }
    
    if quantity:
        log_data['quantity'] = quantity
    if price:
        log_data['price'] = price
    if order_id:
        log_data['order_id'] = order_id
    if api_response:
        # Log essential API response data, truncate if too long
        response_str = json.dumps(api_response)
        log_data['api_response'] = response_str if len(response_str) < 300 else response_str[:300] + "..."
    if error:
        log_data['error'] = error
    
    # Use different log levels based on stage
    if stage in ['FAILED', 'ERROR']:
        logger.error(f"ORDER_{stage.upper()}{_format_data(log_data)}")
    elif stage in ['CONFIRMED', 'PLACED']:
        logger.info(f"ORDER_{stage.upper()}{_format_data(log_data)}")
    else:
        logger.debug(f"ORDER_{stage.upper()}{_format_data(log_data)}")


def log_monitor(event: str, symbol: str = None, current_price: float = None,
                pnl: float = None, pnl_percent: float = None,
                target_price: float = None, stop_loss: float = None,
                decision: str = None, details: Dict = None) -> None:
    """Position monitoring logging for autonomous debugging"""
    log_dir = _get_log_dir()
    logger = _get_logger("monitor", log_dir / "detailed.log")
    
    log_data = {
        'event': event,  # PRICE_CHECK, TARGET_HIT, STOP_LOSS, PNL_UPDATE, etc.
        'timestamp': datetime.now().isoformat()
    }
    
    if symbol:
        log_data['symbol'] = symbol
    if current_price:
        log_data['current_price'] = current_price
    if pnl is not None:
        log_data['pnl'] = pnl
    if pnl_percent is not None:
        log_data['pnl_percent'] = f"{pnl_percent:.2f}%"
    if target_price:
        log_data['target_price'] = target_price
    if stop_loss:
        log_data['stop_loss'] = stop_loss
    if decision:
        log_data['decision'] = decision
    if details:
        log_data.update(details)
    
    logger.info(f"MONITOR_{event.upper()}{_format_data(log_data)}")


def log_rate_limit(action: str, tokens_available: int = None, 
                   wait_time_ms: float = None, api_call: str = None,
                   bucket_status: Dict = None) -> None:
    """Rate limiting diagnostics logging"""
    log_dir = _get_log_dir()
    logger = _get_logger("rate_limiter", log_dir / "detailed.log")
    
    log_data = {
        'action': action,  # TOKEN_CONSUMED, WAIT_REQUIRED, BUCKET_FULL, etc.
        'timestamp': datetime.now().isoformat()
    }
    
    if tokens_available is not None:
        log_data['tokens_available'] = tokens_available
    if wait_time_ms:
        log_data['wait_time_ms'] = wait_time_ms
    if api_call:
        log_data['api_call'] = api_call
    if bucket_status:
        log_data['bucket_status'] = bucket_status
    
    logger.debug(f"RATE_LIMIT_{action.upper()}{_format_data(log_data)}")


def log_error(error_type: str, message: str, exception: Exception = None,
              context: Dict = None, recovery_action: str = None) -> None:
    """Comprehensive error logging with stack traces and context"""
    log_dir = _get_log_dir()
    logger = _get_logger("errors", log_dir / "errors.log")
    
    log_data = {
        'error_type': error_type,
        'message': message,
        'timestamp': datetime.now().isoformat()
    }
    
    if exception:
        log_data['exception_type'] = type(exception).__name__
        log_data['exception_message'] = str(exception)
        log_data['stack_trace'] = traceback.format_exc()
    
    if context:
        log_data['context'] = context
        
    if recovery_action:
        log_data['recovery_action'] = recovery_action
    
    # Also log to main logger for visibility
    main_logger = _get_logger()
    main_logger.error(f"ERROR | {error_type} | {message}")
    
    # Detailed error log
    logger.error(f"DETAILED_ERROR{_format_data(log_data)}")


def log_trade(**kwargs) -> None:
    """Enhanced trade logging with comprehensive details"""
    log_dir = _get_log_dir()
    logger = _get_logger("trades", log_dir / "detailed.log")
    
    # Add timestamp if not present
    if 'timestamp' not in kwargs:
        kwargs['timestamp'] = datetime.now().isoformat()
    
    # Ensure critical fields are present
    required_fields = ['symbol', 'action']
    missing_fields = [field for field in required_fields if field not in kwargs]
    if missing_fields:
        kwargs['missing_fields'] = missing_fields
    
    # Log to both detailed and main log
    trade_summary = _format_data(kwargs)
    logger.info(f"TRADE_COMPLETE{trade_summary}")
    
    # Also log to main logger for visibility
    main_logger = _get_logger()
    symbol = kwargs.get('symbol', 'UNKNOWN')
    action = kwargs.get('action', 'UNKNOWN')
    pnl = kwargs.get('pnl', 'N/A')
    main_logger.info(f"TRADE_SUMMARY | {action} {symbol} | PnL: {pnl}")


def log_analytics(operation: str, details: Dict = None, result: Any = None, 
                  error: str = None, symbol: str = None, 
                  calculation_type: str = None, trade_id: str = None,
                  pnl_realized: float = None, performance_metrics: Dict = None) -> None:
    """Analytics operations logging with enhanced autonomous debugging data"""
    log_dir = _get_log_dir()
    logger = _get_logger("analytics", log_dir / "detailed.log")
    
    log_data = {
        'operation': operation,
        'timestamp': datetime.now().isoformat()
    }
    
    if symbol:
        log_data['symbol'] = symbol
    if calculation_type:
        log_data['calculation_type'] = calculation_type
    if trade_id:
        log_data['trade_id'] = trade_id
    if pnl_realized is not None:
        log_data['pnl_realized'] = pnl_realized
    if performance_metrics:
        log_data['performance_metrics'] = performance_metrics
    
    if details:
        log_data['details'] = details
    if result is not None:
        # Convert result to string if it's complex
        if isinstance(result, (dict, list)):
            log_data['result'] = json.dumps(result) if len(str(result)) < 200 else f"Result: {type(result).__name__} with {len(result) if hasattr(result, '__len__') else 'N/A'} items"
        else:
            log_data['result'] = str(result)
    if error:
        log_data['error'] = error
    
    level = logging.ERROR if error else logging.INFO
    logger.log(level, f"ANALYTICS_{operation.upper()}{_format_data(log_data)}")


def log_database(operation: str, table: str = None, query: str = None,
                 affected_rows: int = None, execution_time_ms: float = None,
                 error: str = None, data: Dict = None) -> None:
    """Database operations logging for autonomous debugging"""
    log_dir = _get_log_dir()
    logger = _get_logger("database", log_dir / "detailed.log")
    
    log_data = {
        'operation': operation,  # INSERT, UPDATE, SELECT, DELETE, CREATE_TABLE, etc.
        'timestamp': datetime.now().isoformat()
    }
    
    if table:
        log_data['table'] = table
    if query:
        # Truncate long queries for readability
        log_data['query'] = query[:200] + '...' if len(query) > 200 else query
    if affected_rows is not None:
        log_data['affected_rows'] = affected_rows
    if execution_time_ms is not None:
        log_data['execution_time_ms'] = execution_time_ms
    if error:
        log_data['error'] = error
    if data:
        log_data['data'] = data
    
    level = logging.ERROR if error else logging.INFO
    logger.log(level, f"DATABASE_{operation.upper()}{_format_data(log_data)}")


def log_file_operation(operation: str, file_path: str, success: bool = True,
                       error: str = None, size_bytes: int = None,
                       processing_time_ms: float = None) -> None:
    """File operations logging for autonomous debugging"""
    log_dir = _get_log_dir()
    logger = _get_logger("file_ops", log_dir / "detailed.log")
    
    log_data = {
        'operation': operation,  # READ, WRITE, DELETE, BACKUP, RESTORE, etc.
        'file_path': file_path,
        'success': success,
        'timestamp': datetime.now().isoformat()
    }
    
    if error:
        log_data['error'] = error
    if size_bytes is not None:
        log_data['size_bytes'] = size_bytes
        log_data['size_mb'] = round(size_bytes / (1024 * 1024), 2)
    if processing_time_ms is not None:
        log_data['processing_time_ms'] = processing_time_ms
    
    level = logging.ERROR if error else logging.INFO
    logger.log(level, f"FILE_{operation.upper()}{_format_data(log_data)}")


def log_configuration(config_name: str, value: any = None, source: str = None,
                      is_default: bool = False, validation_result: str = None) -> None:
    """Configuration loading and validation logging"""
    log_dir = _get_log_dir()
    logger = _get_logger("config", log_dir / "detailed.log")
    
    log_data = {
        'config_name': config_name,
        'timestamp': datetime.now().isoformat(),
        'is_default': is_default
    }
    
    if value is not None:
        # Don't log sensitive values
        if any(sensitive in config_name.lower() for sensitive in ['password', 'token', 'key', 'secret']):
            log_data['value'] = '[REDACTED]'
        else:
            log_data['value'] = str(value)[:100]  # Truncate long values
    
    if source:
        log_data['source'] = source
    if validation_result:
        log_data['validation_result'] = validation_result
    
    logger.info(f"CONFIG_LOADED{_format_data(log_data)}")


def log_session_summary(session_data: Dict) -> None:
    """Log comprehensive session summary for autonomous analysis"""
    log_dir = _get_log_dir()
    logger = _get_logger("session", log_dir / "bot.log")
    
    summary_data = {
        'session_type': 'DAILY_SUMMARY',
        'timestamp': datetime.now().isoformat(),
        **session_data
    }
    
    logger.info(f"SESSION_SUMMARY{_format_data(summary_data)}")


def log_health_check(component: str, status: str, details: Dict = None,
                     response_time_ms: float = None) -> None:
    """System health monitoring logging"""
    log_dir = _get_log_dir()
    logger = _get_logger("health", log_dir / "detailed.log")
    
    log_data = {
        'component': component,  # API, DATABASE, BROKER, MONITOR, etc.
        'status': status,  # HEALTHY, DEGRADED, FAILED, RECOVERING
        'timestamp': datetime.now().isoformat()
    }
    
    if details:
        log_data['details'] = details
    if response_time_ms is not None:
        log_data['response_time_ms'] = response_time_ms
    
    level = logging.ERROR if status in ['FAILED', 'DEGRADED'] else logging.INFO
    logger.log(level, f"HEALTH_CHECK{_format_data(log_data)}")


def log_system_state(component: str, state: str, details: Dict = None) -> None:
    """System state logging for debugging autonomous operation"""
    log_dir = _get_log_dir()
    logger = _get_logger("system", log_dir / "detailed.log")
    
    log_data = {
        'component': component,  # MONITOR, API, BROKER, DATABASE, etc.
        'state': state,          # STARTING, RUNNING, ERROR, STOPPED, etc.
        'timestamp': datetime.now().isoformat()
    }
    
    if details:
        log_data.update(details)
    
    logger.info(f"SYSTEM_{component.upper()}_{state.upper()}{_format_data(log_data)}")


# Convenience function for startup logging
def log_startup_info(additional_info: Dict = None):
    """Log system startup information for debugging"""
    import platform
    import psutil
    import os
    
    startup_info = {
        'python_version': platform.python_version(),
        'system': platform.system(),
        'memory_available_mb': round(psutil.virtual_memory().available / 1024 / 1024),
        'disk_free_gb': round(psutil.disk_usage('/').free / 1024 / 1024 / 1024),
        'cpu_count': psutil.cpu_count(),
        'working_directory': os.getcwd(),
        'log_directory': str(LOG_DIR)
    }
    
    if additional_info:
        startup_info.update(additional_info)
    
    log_system_state("STARTUP", "INITIALIZING", startup_info)
