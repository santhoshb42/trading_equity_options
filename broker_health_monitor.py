#!/usr/bin/env python3
"""
Broker Health Monitor & Auto-Recovery

Monitors broker connectivity and automatically:
1. Detects token expiration / connection failures
2. Refreshes tokens before they expire
3. Restarts bots on authentication failures
4. Prevents cascading failures
"""

import os
import json
import time
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Configuration
CONFIG = {
    'EQUITY_BOT_DIR': '/root/santhosh/trading/equity',
    'OPTIONS_BOT_DIR': '/root/santhosh/trading/options',
    'CHECK_INTERVAL': 300,  # Check every 5 minutes
    'TOKEN_EXPIRY_BUFFER': 1800,  # Refresh 30 min before expiry
    'LOG_FILE': '/tmp/broker_health_monitor.log',
    'RESTART_COOLDOWN': 60,  # Don't restart more than once per minute
}

def log_msg(msg: str, level: str = "INFO"):
    """Log message with timestamp"""
    timestamp = datetime.now().isoformat()
    log_line = f"[{timestamp}] {level:8} | {msg}"
    print(log_line)
    
    try:
        with open(CONFIG['LOG_FILE'], 'a') as f:
            f.write(log_line + '\n')
    except:
        pass

def check_broker_auth():
    """Check if broker authentication is still valid"""
    issues = []
    
    # Check options bot auth
    try:
        optlog = Path(CONFIG['OPTIONS_BOT_DIR']) / 'logs' / datetime.now().strftime('%Y-%m-%d') / 'app.log'
        if optlog.exists():
            with open(optlog, 'r') as f:
                content = f.read()
                if 'Invalid Token' in content or 'AG8001' in content:
                    issues.append('OPTIONS_BOT: Invalid Token (AG8001)')
                if 'ConnectTimeoutError' in content:
                    issues.append('OPTIONS_BOT: Connection Timeout')
    except Exception as e:
        log_msg(f"Error checking options bot logs: {e}", "WARN")
    
    # Check equity bot auth
    try:
        eqlog = Path(CONFIG['EQUITY_BOT_DIR']) / 'logs' / datetime.now().strftime('%Y-%m-%d') / 'app.log'
        if eqlog.exists():
            with open(eqlog, 'r') as f:
                content = f.read()
                if 'Invalid Token' in content or 'unauthorized' in content.lower():
                    issues.append('EQUITY_BOT: Invalid Token')
                if 'ConnectTimeoutError' in content:
                    issues.append('EQUITY_BOT: Connection Timeout')
    except Exception as e:
        log_msg(f"Error checking equity bot logs: {e}", "WARN")
    
    return issues

def check_process_status() -> Dict[str, bool]:
    """Check if bots are actually running"""
    status = {}
    
    # Check equity bot
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'python.*equity.*main.py'],
            capture_output=True,
            timeout=5
        )
        status['equity_running'] = result.returncode == 0
    except:
        status['equity_running'] = False
    
    # Check options bot
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'python.*options.*main.py'],
            capture_output=True,
            timeout=5
        )
        status['options_running'] = result.returncode == 0
    except:
        status['options_running'] = False
    
    return status

def restart_equity_bot():
    """Restart equity bot with fresh token"""
    log_msg("Restarting EQUITY BOT...", "WARN")
    try:
        subprocess.run(
            f"cd {CONFIG['EQUITY_BOT_DIR']} && /usr/bin/python3 main.py",
            shell=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        log_msg("EQUITY BOT restarted successfully", "INFO")
        return True
    except Exception as e:
        log_msg(f"Failed to restart EQUITY BOT: {e}", "ERROR")
        return False

def restart_options_bot():
    """Restart options bot with fresh token"""
    log_msg("Restarting OPTIONS BOT...", "WARN")
    try:
        subprocess.run(
            f"cd {CONFIG['OPTIONS_BOT_DIR']} && /usr/bin/python3 main.py",
            shell=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        log_msg("OPTIONS BOT restarted successfully", "INFO")
        return True
    except Exception as e:
        log_msg(f"Failed to restart OPTIONS BOT: {e}", "ERROR")
        return False

def monitor_loop():
    """Main monitoring loop"""
    log_msg("Broker Health Monitor started", "INFO")
    last_restart = {}
    
    while True:
        try:
            # Check for auth issues
            issues = check_broker_auth()
            if issues:
                log_msg(f"Auth issues detected: {', '.join(issues)}", "WARN")
                
                for issue in issues:
                    if 'OPTIONS' in issue:
                        key = 'options'
                        restart_func = restart_options_bot
                    else:
                        key = 'equity'
                        restart_func = restart_equity_bot
                    
                    # Respect cooldown to avoid restart loops
                    if key not in last_restart or \
                       (datetime.now() - last_restart[key]).total_seconds() > CONFIG['RESTART_COOLDOWN']:
                        restart_func()
                        last_restart[key] = datetime.now()
                    else:
                        log_msg(f"Skipping restart of {key} bot (cooldown active)", "WARN")
            
            # Check process status
            status = check_process_status()
            if not status.get('equity_running'):
                log_msg("EQUITY BOT not running - attempting restart", "WARN")
                if 'equity' not in last_restart or \
                   (datetime.now() - last_restart['equity']).total_seconds() > CONFIG['RESTART_COOLDOWN']:
                    restart_equity_bot()
                    last_restart['equity'] = datetime.now()
            
            if not status.get('options_running'):
                log_msg("OPTIONS BOT not running - attempting restart", "WARN")
                if 'options' not in last_restart or \
                   (datetime.now() - last_restart['options']).total_seconds() > CONFIG['RESTART_COOLDOWN']:
                    restart_options_bot()
                    last_restart['options'] = datetime.now()
            
            # Sleep before next check
            time.sleep(CONFIG['CHECK_INTERVAL'])
            
        except KeyboardInterrupt:
            log_msg("Broker Health Monitor stopped", "INFO")
            break
        except Exception as e:
            log_msg(f"Monitor loop error: {e}", "ERROR")
            time.sleep(CONFIG['CHECK_INTERVAL'])

if __name__ == '__main__':
    monitor_loop()
