#!/usr/bin/env python3
"""
Alert System - TradingView Style Alerts for Equity & Options Bots

Monitors:
1. PROCESS - Bot health, uptime, crashes
2. ORDER - Trade execution, fills, rejections
3. MONITOR - P&L, positions, risk exposure

Supports multiple channels:
- Console (colored)
- Log files
- Telegram (if configured)
- Email (if configured)
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, asdict
import threading
import time
from collections import defaultdict

# =============================================================================
# Alert Configuration
# =============================================================================

class AlertLevel(Enum):
    """Alert severity levels"""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"

class AlertChannel(Enum):
    """Alert delivery channels"""
    CONSOLE = "CONSOLE"
    LOG = "LOG"
    TELEGRAM = "TELEGRAM"
    EMAIL = "EMAIL"

class AlertCategory(Enum):
    """Alert categories for filtering"""
    PROCESS = "PROCESS"
    ORDER = "ORDER"
    MONITOR = "MONITOR"
    SYSTEM = "SYSTEM"
    RISK = "RISK"

# =============================================================================
# Alert Data Structure
# =============================================================================

@dataclass
class Alert:
    """Standard alert structure"""
    timestamp: str
    category: str  # PROCESS, ORDER, MONITOR
    level: str     # INFO, WARNING, CRITICAL, ERROR, SUCCESS
    bot_type: str  # equity, options
    title: str
    message: str
    details: Dict[str, Any]
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), indent=2)

# =============================================================================
# Alert Manager
# =============================================================================

class AlertManager:
    """Main alert manager for both equity and options bots"""
    
    def __init__(self, workspace_root: str = "/root/santhosh/trading"):
        self.workspace_root = Path(workspace_root)
        self.alert_log_dir = self.workspace_root / "logs" / "alerts"
        self.alert_log_dir.mkdir(parents=True, exist_ok=True)
        
        # Alert tracking
        self.alerts_history: Dict[str, List[Alert]] = defaultdict(list)
        self.process_status: Dict[str, Dict] = {
            'equity': {'running': False, 'started_at': None, 'crashes': 0},
            'options': {'running': False, 'started_at': None, 'crashes': 0}
        }
        self.order_stats: Dict[str, Dict] = {
            'equity': {'total': 0, 'filled': 0, 'rejected': 0, 'pending': 0},
            'options': {'total': 0, 'filled': 0, 'rejected': 0, 'pending': 0}
        }
        
        # Thresholds
        self.crash_threshold = 2  # Alert after N crashes
        self.rejection_threshold = 5  # Alert after N rejections in a row
        self.max_pending_orders = 10
        
        # Setup logging
        self._setup_logging()
        
        # Lock for thread safety
        self.lock = threading.Lock()
    
    def _setup_logging(self):
        """Setup logging for alerts"""
        self.logger = logging.getLogger('AlertSystem')
        self.logger.setLevel(logging.DEBUG)
        
        # File handler
        alert_file = self.alert_log_dir / f"alerts_{datetime.now().strftime('%Y%m%d')}.log"
        fh = logging.FileHandler(alert_file)
        fh.setLevel(logging.DEBUG)
        
        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        self.logger.addHandler(fh)
        self.logger.addHandler(ch)
    
    def send_alert(self, 
                   category: str,
                   bot_type: str,
                   level: str,
                   title: str,
                   message: str,
                   details: Optional[Dict] = None) -> Alert:
        """
        Send an alert (thread-safe)
        
        Args:
            category: PROCESS, ORDER, MONITOR, SYSTEM, RISK
            bot_type: 'equity' or 'options'
            level: INFO, WARNING, CRITICAL, ERROR, SUCCESS
            title: Short alert title
            message: Detailed message
            details: Additional context
        
        Returns:
            Alert object
        """
        with self.lock:
            alert = Alert(
                timestamp=datetime.now().isoformat(),
                category=category,
                level=level,
                bot_type=bot_type,
                title=title,
                message=message,
                details=details or {}
            )
            
            # Store in history
            self.alerts_history[f"{bot_type}_{category}"].append(alert)
            
            # Log to file
            self._log_alert(alert)
            
            # Send to channels
            self._dispatch_alert(alert)
            
            return alert
    
    def _log_alert(self, alert: Alert):
        """Log alert to file"""
        log_msg = f"[{alert.bot_type.upper()}] [{alert.category}] [{alert.level}] {alert.title}\n{alert.message}"
        
        if alert.level == AlertLevel.CRITICAL.value:
            self.logger.critical(log_msg)
        elif alert.level == AlertLevel.ERROR.value:
            self.logger.error(log_msg)
        elif alert.level == AlertLevel.WARNING.value:
            self.logger.warning(log_msg)
        else:
            self.logger.info(log_msg)
    
    def _dispatch_alert(self, alert: Alert):
        """Send alert to configured channels"""
        # Console output with colors
        self._console_alert(alert)
        
        # Telegram (if configured)
        if os.getenv('TELEGRAM_BOT_TOKEN'):
            self._telegram_alert(alert)
        
        # Email (if configured)
        if os.getenv('ALERT_EMAIL'):
            self._email_alert(alert)
    
    def _console_alert(self, alert: Alert):
        """Print colored alert to console"""
        colors = {
            'INFO': '\033[94m',      # Blue
            'SUCCESS': '\033[92m',   # Green
            'WARNING': '\033[93m',   # Yellow
            'ERROR': '\033[91m',     # Red
            'CRITICAL': '\033[41m'   # Red background
        }
        reset = '\033[0m'
        
        color = colors.get(alert.level, reset)
        timestamp = alert.timestamp.split('T')[1][:8]
        
        print(f"\n{color}{'='*70}")
        print(f"⏰ {timestamp} | 🤖 {alert.bot_type.upper()} | 📊 {alert.category}")
        print(f"{'='*70}")
        print(f"Title:   {alert.title}")
        print(f"Message: {alert.message}")
        if alert.details:
            print(f"Details: {json.dumps(alert.details, indent=2)}")
        print(f"{'='*70}{reset}\n")
    
    def _telegram_alert(self, alert: Alert):
        """Send alert via Telegram (stub - implement based on your Telegram setup)"""
        try:
            import requests
            
            token = os.getenv('TELEGRAM_BOT_TOKEN')
            chat_id = os.getenv('TELEGRAM_CHAT_ID')
            
            if not (token and chat_id):
                return
            
            # Format message for Telegram
            emoji_map = {
                'PROCESS': '🔄',
                'ORDER': '📈',
                'MONITOR': '📊',
                'SYSTEM': '⚙️',
                'RISK': '⚠️'
            }
            
            level_emoji = {
                'INFO': 'ℹ️',
                'SUCCESS': '✅',
                'WARNING': '⚠️',
                'ERROR': '❌',
                'CRITICAL': '🚨'
            }
            
            emoji = emoji_map.get(alert.category, '📍')
            level_ico = level_emoji.get(alert.level, '•')
            
            message = f"{emoji} {level_ico} **{alert.bot_type.upper()}**\n"
            message += f"**{alert.title}**\n"
            message += f"{alert.message}\n"
            
            if alert.details:
                for key, value in alert.details.items():
                    message += f"• {key}: {value}\n"
            
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'Markdown'
            }
            
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            self.logger.warning(f"Failed to send Telegram alert: {e}")
    
    def _email_alert(self, alert: Alert):
        """Send alert via email (stub - implement based on your email setup)"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            
            smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
            smtp_port = int(os.getenv('SMTP_PORT', 587))
            sender = os.getenv('ALERT_EMAIL_FROM')
            recipient = os.getenv('ALERT_EMAIL')
            password = os.getenv('ALERT_EMAIL_PASSWORD')
            
            if not all([sender, recipient, password]):
                return
            
            subject = f"[{alert.bot_type.upper()}] {alert.category} - {alert.title}"
            body = f"""
ALERT: {alert.title}

BOT: {alert.bot_type.upper()}
CATEGORY: {alert.category}
LEVEL: {alert.level}
TIME: {alert.timestamp}

MESSAGE:
{alert.message}

DETAILS:
{json.dumps(alert.details, indent=2)}
            """
            
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = sender
            msg['To'] = recipient
            
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(msg)
        except Exception as e:
            self.logger.warning(f"Failed to send email alert: {e}")
    
    # =================================================================
    # PROCESS ALERTS
    # =================================================================
    
    def alert_bot_started(self, bot_type: str, config: Dict):
        """Bot startup alert"""
        self.process_status[bot_type]['running'] = True
        self.process_status[bot_type]['started_at'] = datetime.now()
        
        return self.send_alert(
            category=AlertCategory.PROCESS.value,
            bot_type=bot_type,
            level=AlertLevel.SUCCESS.value,
            title=f"{bot_type.upper()} Bot Started",
            message=f"Trading bot is now running",
            details={
                'started_at': self.process_status[bot_type]['started_at'].isoformat(),
                'mode': config.get('mode', 'unknown'),
                'capital': config.get('capital', 0)
            }
        )
    
    def alert_bot_stopped(self, bot_type: str, reason: str = None):
        """Bot shutdown alert"""
        self.process_status[bot_type]['running'] = False
        uptime = None
        
        if self.process_status[bot_type]['started_at']:
            uptime = (datetime.now() - self.process_status[bot_type]['started_at']).total_seconds()
        
        return self.send_alert(
            category=AlertCategory.PROCESS.value,
            bot_type=bot_type,
            level=AlertLevel.WARNING.value,
            title=f"{bot_type.upper()} Bot Stopped",
            message=f"Trading bot has stopped",
            details={
                'stopped_at': datetime.now().isoformat(),
                'reason': reason or 'unknown',
                'uptime_seconds': uptime
            }
        )
    
    def alert_bot_crash(self, bot_type: str, error: str, traceback_str: str = None):
        """Bot crash alert"""
        self.process_status[bot_type]['crashes'] += 1
        crashes = self.process_status[bot_type]['crashes']
        
        level = AlertLevel.CRITICAL.value if crashes >= self.crash_threshold else AlertLevel.ERROR.value
        
        return self.send_alert(
            category=AlertCategory.PROCESS.value,
            bot_type=bot_type,
            level=level,
            title=f"{bot_type.upper()} Bot Crashed (#{crashes})",
            message=f"Bot process crashed: {error}",
            details={
                'crash_count': crashes,
                'error': error,
                'traceback': traceback_str,
                'crashed_at': datetime.now().isoformat()
            }
        )
    
    def alert_process_unhealthy(self, bot_type: str, reason: str, metrics: Dict):
        """Process health check alert"""
        return self.send_alert(
            category=AlertCategory.PROCESS.value,
            bot_type=bot_type,
            level=AlertLevel.WARNING.value,
            title=f"{bot_type.upper()} Process Health Degraded",
            message=reason,
            details=metrics
        )
    
    def alert_process_recovered(self, bot_type: str, what: str):
        """Process recovery alert"""
        return self.send_alert(
            category=AlertCategory.PROCESS.value,
            bot_type=bot_type,
            level=AlertLevel.SUCCESS.value,
            title=f"{bot_type.upper()} Process Recovered",
            message=f"Recovered from: {what}",
            details={'recovered_at': datetime.now().isoformat()}
        )
    
    # =================================================================
    # ORDER ALERTS
    # =================================================================
    
    def alert_order_placed(self, bot_type: str, order_details: Dict):
        """Order placement alert"""
        self.order_stats[bot_type]['total'] += 1
        self.order_stats[bot_type]['pending'] += 1
        
        return self.send_alert(
            category=AlertCategory.ORDER.value,
            bot_type=bot_type,
            level=AlertLevel.INFO.value,
            title=f"Order Placed - {order_details.get('symbol', 'N/A')}",
            message=f"{order_details.get('action', 'BUY')} {order_details.get('quantity', 0)} units @ {order_details.get('price', 0)}",
            details={
                'order_id': order_details.get('order_id'),
                'symbol': order_details.get('symbol'),
                'action': order_details.get('action'),
                'quantity': order_details.get('quantity'),
                'price': order_details.get('price'),
                'exchange': order_details.get('exchange'),
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def alert_order_filled(self, bot_type: str, order_details: Dict):
        """Order fill alert"""
        self.order_stats[bot_type]['filled'] += 1
        self.order_stats[bot_type]['pending'] = max(0, self.order_stats[bot_type]['pending'] - 1)
        
        return self.send_alert(
            category=AlertCategory.ORDER.value,
            bot_type=bot_type,
            level=AlertLevel.SUCCESS.value,
            title=f"Order Filled - {order_details.get('symbol', 'N/A')}",
            message=f"✅ {order_details.get('action', 'BUY')} {order_details.get('quantity', 0)} @ {order_details.get('fill_price', 0)}",
            details={
                'order_id': order_details.get('order_id'),
                'symbol': order_details.get('symbol'),
                'quantity': order_details.get('quantity'),
                'fill_price': order_details.get('fill_price'),
                'fill_time': order_details.get('fill_time'),
                'pnl': order_details.get('pnl')
            }
        )
    
    def alert_order_rejected(self, bot_type: str, order_details: Dict, reason: str):
        """Order rejection alert"""
        self.order_stats[bot_type]['rejected'] += 1
        self.order_stats[bot_type]['pending'] = max(0, self.order_stats[bot_type]['pending'] - 1)
        
        return self.send_alert(
            category=AlertCategory.ORDER.value,
            bot_type=bot_type,
            level=AlertLevel.ERROR.value,
            title=f"Order Rejected - {order_details.get('symbol', 'N/A')}",
            message=f"❌ {reason}",
            details={
                'order_id': order_details.get('order_id'),
                'symbol': order_details.get('symbol'),
                'reason': reason,
                'action': order_details.get('action'),
                'quantity': order_details.get('quantity'),
                'price': order_details.get('price')
            }
        )
    
    def alert_order_pending(self, bot_type: str):
        """Alert if too many pending orders"""
        pending = self.order_stats[bot_type]['pending']
        
        if pending > self.max_pending_orders:
            return self.send_alert(
                category=AlertCategory.ORDER.value,
                bot_type=bot_type,
                level=AlertLevel.WARNING.value,
                title="High Pending Orders",
                message=f"⚠️ {pending} orders waiting for execution",
                details={
                    'pending_count': pending,
                    'threshold': self.max_pending_orders,
                    'total_orders': self.order_stats[bot_type]['total']
                }
            )
    
    # =================================================================
    # MONITOR ALERTS
    # =================================================================
    
    def alert_position_opened(self, bot_type: str, position: Dict):
        """New position opened alert"""
        return self.send_alert(
            category=AlertCategory.MONITOR.value,
            bot_type=bot_type,
            level=AlertLevel.INFO.value,
            title=f"Position Opened - {position.get('symbol', 'N/A')}",
            message=f"{position.get('action', 'BUY')} {position.get('quantity', 0)} units",
            details={
                'symbol': position.get('symbol'),
                'entry_price': position.get('entry_price'),
                'quantity': position.get('quantity'),
                'sl': position.get('sl'),
                'target': position.get('target'),
                'opened_at': datetime.now().isoformat()
            }
        )
    
    def alert_position_closed(self, bot_type: str, position: Dict):
        """Position closed alert"""
        pnl = position.get('pnl', 0)
        pnl_pct = position.get('pnl_pct', 0)
        
        level = AlertLevel.SUCCESS.value if pnl > 0 else AlertLevel.WARNING.value
        emoji = "✅" if pnl > 0 else "⚠️"
        
        return self.send_alert(
            category=AlertCategory.MONITOR.value,
            bot_type=bot_type,
            level=level,
            title=f"Position Closed - {position.get('symbol', 'N/A')}",
            message=f"{emoji} {position.get('exit_reason', 'Exit')} | P&L: ₹{pnl:.2f} ({pnl_pct:.2f}%)",
            details={
                'symbol': position.get('symbol'),
                'entry_price': position.get('entry_price'),
                'exit_price': position.get('exit_price'),
                'quantity': position.get('quantity'),
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_reason': position.get('exit_reason'),
                'holding_time': position.get('holding_time')
            }
        )
    
    def alert_daily_pnl(self, bot_type: str, pnl_data: Dict):
        """Daily P&L summary alert"""
        total_pnl = pnl_data.get('total_pnl', 0)
        level = AlertLevel.SUCCESS.value if total_pnl > 0 else AlertLevel.WARNING.value
        
        return self.send_alert(
            category=AlertCategory.MONITOR.value,
            bot_type=bot_type,
            level=level,
            title=f"Daily P&L Summary",
            message=f"Total P&L: ₹{total_pnl:.2f}",
            details=pnl_data
        )
    
    def alert_risk_limit_breach(self, bot_type: str, risk_data: Dict):
        """Risk limit breach alert"""
        return self.send_alert(
            category=AlertCategory.RISK.value,
            bot_type=bot_type,
            level=AlertLevel.CRITICAL.value,
            title="Risk Limit Breach",
            message=f"⚠️ Risk exposure exceeds limit",
            details=risk_data
        )
    
    def alert_drawdown_warning(self, bot_type: str, drawdown_data: Dict):
        """Drawdown warning alert"""
        drawdown_pct = drawdown_data.get('drawdown_pct', 0)
        level = AlertLevel.CRITICAL.value if drawdown_pct > 10 else AlertLevel.WARNING.value
        
        return self.send_alert(
            category=AlertCategory.RISK.value,
            bot_type=bot_type,
            level=level,
            title=f"Drawdown Warning - {drawdown_pct:.2f}%",
            message=f"Current drawdown: {drawdown_pct:.2f}%",
            details=drawdown_data
        )
    
    def alert_capital_low(self, bot_type: str, capital_data: Dict):
        """Low capital alert"""
        return self.send_alert(
            category=AlertCategory.RISK.value,
            bot_type=bot_type,
            level=AlertLevel.WARNING.value,
            title="Available Capital Low",
            message=f"Available capital: ₹{capital_data.get('available', 0):.2f}",
            details=capital_data
        )
    
    # =================================================================
    # System Alerts
    # =================================================================
    
    def alert_rate_limit(self, bot_type: str, limit_data: Dict):
        """Rate limit hit alert"""
        return self.send_alert(
            category=AlertCategory.SYSTEM.value,
            bot_type=bot_type,
            level=AlertLevel.WARNING.value,
            title="Rate Limit Hit",
            message=f"API rate limit exceeded",
            details=limit_data
        )
    
    def alert_connection_lost(self, bot_type: str, service: str):
        """Connection lost alert"""
        return self.send_alert(
            category=AlertCategory.SYSTEM.value,
            bot_type=bot_type,
            level=AlertLevel.CRITICAL.value,
            title="Connection Lost",
            message=f"Lost connection to {service}",
            details={'service': service}
        )
    
    def alert_connection_restored(self, bot_type: str, service: str):
        """Connection restored alert"""
        return self.send_alert(
            category=AlertCategory.SYSTEM.value,
            bot_type=bot_type,
            level=AlertLevel.SUCCESS.value,
            title="Connection Restored",
            message=f"Reconnected to {service}",
            details={'service': service}
        )
    
    # =================================================================
    # Reporting
    # =================================================================
    
    def get_stats(self, bot_type: str) -> Dict:
        """Get stats for a bot"""
        return {
            'process': self.process_status.get(bot_type, {}),
            'orders': self.order_stats.get(bot_type, {}),
            'timestamp': datetime.now().isoformat()
        }
    
    def get_alert_summary(self, bot_type: str = None, hours: int = 1) -> Dict:
        """Get alert summary for the past N hours"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        summary = {
            'timestamp': datetime.now().isoformat(),
            'period_hours': hours,
            'counts': defaultdict(int),
            'alerts': []
        }
        
        for key, alerts in self.alerts_history.items():
            if bot_type and not key.startswith(bot_type):
                continue
            
            for alert in alerts:
                alert_time = datetime.fromisoformat(alert.timestamp)
                if alert_time >= cutoff_time:
                    summary['counts'][alert.level] += 1
                    summary['alerts'].append({
                        'timestamp': alert.timestamp,
                        'bot': alert.bot_type,
                        'category': alert.category,
                        'level': alert.level,
                        'title': alert.title
                    })
        
        return summary

# =============================================================================
# Global Alert Manager Instance
# =============================================================================

_alert_manager: Optional[AlertManager] = None

def get_alert_manager(workspace_root: str = "/root/santhosh/trading") -> AlertManager:
    """Get or create the global alert manager"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager(workspace_root)
    return _alert_manager

# =============================================================================
# Convenience Functions
# =============================================================================

def alert_bot_started(bot_type: str, config: Dict):
    """Alert: Bot startup"""
    return get_alert_manager().alert_bot_started(bot_type, config)

def alert_bot_stopped(bot_type: str, reason: str = None):
    """Alert: Bot shutdown"""
    return get_alert_manager().alert_bot_stopped(bot_type, reason)

def alert_bot_crash(bot_type: str, error: str, traceback_str: str = None):
    """Alert: Bot crash"""
    return get_alert_manager().alert_bot_crash(bot_type, error, traceback_str)

def alert_order_placed(bot_type: str, order_details: Dict):
    """Alert: Order placed"""
    return get_alert_manager().alert_order_placed(bot_type, order_details)

def alert_order_filled(bot_type: str, order_details: Dict):
    """Alert: Order filled"""
    return get_alert_manager().alert_order_filled(bot_type, order_details)

def alert_order_rejected(bot_type: str, order_details: Dict, reason: str):
    """Alert: Order rejected"""
    return get_alert_manager().alert_order_rejected(bot_type, order_details, reason)

def alert_position_opened(bot_type: str, position: Dict):
    """Alert: Position opened"""
    return get_alert_manager().alert_position_opened(bot_type, position)

def alert_position_closed(bot_type: str, position: Dict):
    """Alert: Position closed"""
    return get_alert_manager().alert_position_closed(bot_type, position)

def alert_daily_pnl(bot_type: str, pnl_data: Dict):
    """Alert: Daily P&L"""
    return get_alert_manager().alert_daily_pnl(bot_type, pnl_data)

def alert_risk_limit_breach(bot_type: str, risk_data: Dict):
    """Alert: Risk limit breach"""
    return get_alert_manager().alert_risk_limit_breach(bot_type, risk_data)

def alert_drawdown_warning(bot_type: str, drawdown_data: Dict):
    """Alert: Drawdown warning"""
    return get_alert_manager().alert_drawdown_warning(bot_type, drawdown_data)

def alert_rate_limit(bot_type: str, limit_data: Dict):
    """Alert: Rate limit hit"""
    return get_alert_manager().alert_rate_limit(bot_type, limit_data)

def alert_connection_lost(bot_type: str, service: str):
    """Alert: Connection lost"""
    return get_alert_manager().alert_connection_lost(bot_type, service)

def alert_connection_restored(bot_type: str, service: str):
    """Alert: Connection restored"""
    return get_alert_manager().alert_connection_restored(bot_type, service)

if __name__ == "__main__":
    # Test the alert system
    manager = get_alert_manager()
    
    # Test process alerts
    print("Testing Process Alerts...")
    manager.alert_bot_started('equity', {'mode': 'LIVE', 'capital': 100000})
    manager.alert_bot_crash('equity', 'Connection timeout', 'Traceback...')
    
    # Test order alerts
    print("\nTesting Order Alerts...")
    manager.alert_order_placed('equity', {
        'order_id': 'ORD001',
        'symbol': 'SBIN',
        'action': 'BUY',
        'quantity': 10,
        'price': 500.50
    })
    
    manager.alert_order_filled('equity', {
        'order_id': 'ORD001',
        'symbol': 'SBIN',
        'quantity': 10,
        'fill_price': 500.60,
        'fill_time': datetime.now().isoformat()
    })
    
    # Test monitor alerts
    print("\nTesting Monitor Alerts...")
    manager.alert_position_opened('equity', {
        'symbol': 'SBIN',
        'action': 'BUY',
        'quantity': 10,
        'entry_price': 500.60,
        'sl': 495.00,
        'target': 510.00
    })
    
    manager.alert_daily_pnl('equity', {
        'total_pnl': 2500.00,
        'trades': 5,
        'winners': 3,
        'losers': 2,
        'win_rate': 0.60
    })
    
    # Test risk alerts
    print("\nTesting Risk Alerts...")
    manager.alert_drawdown_warning('equity', {
        'drawdown_pct': 5.5,
        'peak': 100000,
        'current': 94500,
        'threshold': 10
    })
    
    print("\n✅ Alert system test complete!")
    print(f"\nAlert Summary:\n{json.dumps(manager.get_alert_summary('equity'), indent=2)}")
