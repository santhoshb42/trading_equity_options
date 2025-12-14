#!/usr/bin/env python3
"""
Bot Broker Login Status Check - Ultimate Health & Status Report

Checks the actual broker authentication status of both bots by examining:
1. Broker login success/failure
2. API connectivity
3. Session validity
4. Token expiration
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

class BotHealthChecker:
    """Check broker login status of trading bots"""
    
    def __init__(self):
        self.equity_dir = Path('/root/santhosh/trading/equity')
        self.options_dir = Path('/root/santhosh/trading/options')
        self.report = {}
    
    def check_equity_bot(self) -> Dict:
        """Check equity bot broker login status"""
        status = {
            'name': 'EQUITY BOT',
            'process_running': False,
            'broker_logged_in': False,
            'api_connected': False,
            'session_valid': False,
            'last_activity': None,
            'errors': [],
            'warnings': [],
            'details': {}
        }
        
        # Check process
        try:
            result = os.popen('pgrep -f "equity.*main.py"').read().strip()
            status['process_running'] = bool(result)
            if result:
                status['details']['pid'] = result
        except:
            pass
        
        # Check broker login
        try:
            stat_log = self.equity_dir / 'logs' / datetime.now().strftime('%Y-%m-%d') / 'statistics.log'
            if stat_log.exists():
                with open(stat_log, 'r') as f:
                    content = f.read()
                    if 'BROKER_LOGIN' in content and '✅ Login successful' in content:
                        status['broker_logged_in'] = True
                    if 'HOLDINGS_SYNC' in content:
                        status['api_connected'] = True
                    # Get last activity time
                    lines = content.strip().split('\n')
                    if lines:
                        last_line = lines[-1]
                        if '|' in last_line:
                            timestamp = last_line.split('|')[0].strip()
                            status['last_activity'] = timestamp
        except Exception as e:
            status['errors'].append(f"Error reading equity logs: {e}")
        
        # Check for auth errors
        try:
            bot_log = self.equity_dir / 'logs' / datetime.now().strftime('%Y-%m-%d') / 'bot.log'
            if bot_log.exists():
                with open(bot_log, 'r') as f:
                    content = f.read()
                    if 'Invalid Token' in content or 'AG8001' in content:
                        status['errors'].append("Invalid broker token detected")
                    if 'ConnectTimeoutError' in content:
                        status['errors'].append("Broker connection timeout")
                    if 'ERROR' in content:
                        error_lines = [l for l in content.split('\n') if 'ERROR' in l]
                        if error_lines:
                            status['details']['recent_errors'] = error_lines[-3:]
        except:
            pass
        
        # Validate session
        status['session_valid'] = status['broker_logged_in'] and len(status['errors']) == 0
        
        return status
    
    def check_options_bot(self) -> Dict:
        """Check options bot broker login status"""
        status = {
            'name': 'OPTIONS BOT',
            'process_running': False,
            'broker_logged_in': False,
            'api_connected': False,
            'session_valid': False,
            'last_activity': None,
            'errors': [],
            'warnings': [],
            'details': {},
            'initialization_issue': None
        }
        
        # Check process
        try:
            result = os.popen('pgrep -f "options.*main.py"').read().strip()
            status['process_running'] = bool(result)
            if result:
                status['details']['pid'] = result
        except:
            pass
        
        # Check initialization status
        try:
            optbot_log = self.options_dir / 'logs' / datetime.now().strftime('%Y-%m-%d') / 'optbot.log'
            if optbot_log.exists():
                with open(optbot_log, 'r') as f:
                    lines = f.readlines()
                    
                    # Find the LATEST BOT_INIT: START position
                    latest_init_idx = -1
                    for i in range(len(lines)-1, -1, -1):
                        if 'BOT_INIT: START' in lines[i]:
                            latest_init_idx = i
                            break
                    
                    # Only check from latest initialization onwards
                    if latest_init_idx >= 0:
                        latest_lines = lines[latest_init_idx:]
                        
                        # Check if latest init completed successfully
                        init_complete = any('BOT_INIT: COMPLETE' in l for l in latest_lines)
                        
                        if init_complete:
                            # Initialization succeeded
                            status['broker_logged_in'] = True
                            status['api_connected'] = True
                            status['initialization_issue'] = None
                        else:
                            # Check if stuck looping (another START before COMPLETE)
                            start_count = sum(1 for l in latest_lines if 'BOT_INIT: START' in l)
                            if start_count > 1:
                                status['initialization_issue'] = f"Init loop detected (restarting)"
                                status['errors'].append("Bot initialization loop detected")
                    
                    # Get last activity
                    for line in reversed(lines[-50:]):
                        if '|' in line and ('INFO' in line or 'DEBUG' in line):
                            timestamp = line.split('|')[0].strip()
                            status['last_activity'] = timestamp
                            break
                    
                    # Check for auth errors in entire log
                    all_content = ''.join(lines)
                    if 'Invalid Token' in all_content and 'BOT_INIT: COMPLETE' not in all_content:
                        status['errors'].append("Invalid broker token detected")
                    if 'AG8001' in all_content and 'BOT_INIT: COMPLETE' not in all_content:
                        status['errors'].append("AngelOne AG8001 error (token expired)")
                    if 'ConnectTimeoutError' in all_content and 'BOT_INIT: COMPLETE' not in all_content:
                        status['errors'].append("Broker connection timeout")
                    
        except Exception as e:
            status['errors'].append(f"Error reading options logs: {e}")
        
        # Validate session
        status['session_valid'] = status['broker_logged_in'] and len(status['errors']) == 0
        
        return status
    
    def check_health_monitor(self) -> Dict:
        """Check if health monitor is running"""
        status = {
            'name': 'BROKER HEALTH MONITOR',
            'running': False,
            'last_check': None,
            'status': 'UNKNOWN'
        }
        
        try:
            result = os.popen('pgrep -f "broker_health_monitor"').read().strip()
            status['running'] = bool(result)
        except:
            pass
        
        try:
            monitor_log = Path('/tmp/broker_health_monitor.log')
            if monitor_log.exists():
                with open(monitor_log, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        # Get last status
                        for line in reversed(lines[-10:]):
                            if 'INFO' in line or 'WARN' in line or 'ERROR' in line:
                                status['last_check'] = line.strip()
                                if 'ERROR' in line:
                                    status['status'] = 'ISSUE DETECTED'
                                elif 'WARN' in line:
                                    status['status'] = 'MONITORING'
                                elif 'INFO' in line:
                                    status['status'] = 'HEALTHY'
                                break
        except:
            pass
        
        return status
    
    def print_report(self):
        """Print comprehensive health report"""
        equity = self.check_equity_bot()
        options = self.check_options_bot()
        monitor = self.check_health_monitor()
        
        print("\n")
        print("╔════════════════════════════════════════════════════════════════════════════╗")
        print("║            🔐 BROKER LOGIN STATUS - ULTIMATE HEALTH REPORT                  ║")
        print("╚════════════════════════════════════════════════════════════════════════════╝")
        
        # Equity Bot Status
        self._print_bot_status(equity)
        
        # Options Bot Status  
        self._print_bot_status(options)
        
        # Health Monitor Status
        self._print_monitor_status(monitor)
        
        # Summary
        self._print_summary(equity, options, monitor)
    
    def _print_bot_status(self, bot_status: Dict):
        """Print single bot status"""
        name = bot_status['name']
        
        # Status indicators
        process = "✅ RUNNING" if bot_status['process_running'] else "❌ NOT RUNNING"
        broker = "✅ LOGGED IN" if bot_status['broker_logged_in'] else "❌ NOT LOGGED IN"
        session = "✅ VALID" if bot_status['session_valid'] else "❌ INVALID"
        
        print(f"\n┌─ {name} ────────────────────────────────────────────────────────┐")
        print(f"│ Process:        {process:<50} │")
        print(f"│ Broker Login:   {broker:<50} │")
        print(f"│ Session Status: {session:<50} │")
        
        if bot_status['last_activity']:
            print(f"│ Last Activity:  {bot_status['last_activity']:<50} │")
        
        if bot_status.get('initialization_issue'):
            print(f"│ ⚠️  Init Issue:  {bot_status['initialization_issue']:<50} │")
        
        if bot_status['errors']:
            print(f"│ 🚨 Errors:      │")
            for err in bot_status['errors']:
                print(f"│   • {err:<56} │")
        
        if bot_status['warnings']:
            print(f"│ ⚠️  Warnings:    │")
            for warn in bot_status['warnings']:
                print(f"│   • {warn:<56} │")
        
        print(f"└──────────────────────────────────────────────────────────────────────┘")
    
    def _print_monitor_status(self, monitor_status: Dict):
        """Print health monitor status"""
        running = "✅ RUNNING" if monitor_status['running'] else "❌ NOT RUNNING"
        
        print(f"\n┌─ {monitor_status['name']} ────────────────────────────────────────────┐")
        print(f"│ Status:     {running:<54} │")
        print(f"│ Status:     {monitor_status['status']:<54} │")
        
        if monitor_status['last_check']:
            print(f"│ Last Check: {monitor_status['last_check'][:60]:<60} │")
        
        print(f"└──────────────────────────────────────────────────────────────────────┘")
    
    def _print_summary(self, equity: Dict, options: Dict, monitor: Dict):
        """Print overall system summary"""
        all_systems = [
            (equity['name'], equity['session_valid']),
            (options['name'], options['session_valid']),
            (monitor['name'], monitor['running'])
        ]
        
        all_healthy = all(healthy for _, healthy in all_systems)
        
        print(f"\n╔════════════════════════════════════════════════════════════════════════════╗")
        if all_healthy:
            print(f"║ 🟢 OVERALL STATUS: HEALTHY - ALL SYSTEMS OPERATIONAL                     ║")
        else:
            print(f"║ 🔴 OVERALL STATUS: ISSUES DETECTED - SEE ABOVE FOR DETAILS                ║")
        print(f"╚════════════════════════════════════════════════════════════════════════════╝")
        
        print(f"\n📋 SYSTEM SUMMARY:")
        for name, healthy in all_systems:
            symbol = "✅" if healthy else "❌"
            print(f"   {symbol} {name}")
        
        print(f"\n📊 RECOMMENDATIONS:")
        if not equity['session_valid']:
            print(f"   • EQUITY BOT: Check logs at /root/santhosh/trading/equity/logs/2025-12-14/statistics.log")
        if not options['session_valid']:
            print(f"   • OPTIONS BOT: Check logs at /root/santhosh/trading/options/logs/2025-12-14/optbot.log")
            if options['initialization_issue']:
                print(f"     Issue: {options['initialization_issue']}")
                print(f"     Action: pkill -f 'options.*main' && sleep 2 && bash /root/santhosh/trading/start_bots_robust.sh")
        if not monitor['running']:
            print(f"   • HEALTH MONITOR: Restart monitoring")
            print(f"     Action: python3 /root/santhosh/trading/broker_health_monitor.py &")
        
        print(f"\n🔗 Log Files:")
        print(f"   • Health Monitor: tail -f /tmp/broker_health_monitor.log")
        print(f"   • Equity Bot:     tail -f /root/santhosh/trading/equity/logs/2025-12-14/statistics.log")
        print(f"   • Options Bot:    tail -f /root/santhosh/trading/options/logs/2025-12-14/optbot.log")

if __name__ == '__main__':
    checker = BotHealthChecker()
    checker.print_report()
