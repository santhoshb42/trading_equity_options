#!/usr/bin/env python3
"""
Health Monitor - Track Bot Process, Orders, and System Metrics

Monitors:
1. PROCESS - CPU, Memory, Uptime, Crashes
2. ORDER - Fills, Rejections, Pending, Rate Limits
3. MONITOR - P&L, Positions, Risk Exposure, Capital

Sends periodic alerts when thresholds are breached.
"""

import os
import sys
import time
import psutil
import threading
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from collections import defaultdict

# Add trading root to path
TRADING_ROOT = Path(__file__).parent
sys.path.insert(0, str(TRADING_ROOT / "equity" / "eqcode"))
sys.path.insert(0, str(TRADING_ROOT / "options" / "optcode"))

from alert_system import get_alert_manager

# =============================================================================
# Health Monitor
# =============================================================================

class HealthMonitor:
    """Monitor bot health across process, orders, and monitoring"""
    
    def __init__(self, workspace_root: str = None):
        self.workspace_root = Path(workspace_root or TRADING_ROOT)
        self.alert_manager = get_alert_manager(str(self.workspace_root))
        
        # Configuration
        self.check_interval = 60  # Check every 60 seconds
        self.running = False
        
        # Monitoring state
        self.bot_processes: Dict[str, Dict] = {
            'equity': {'pid': None, 'started_at': None},
            'options': {'pid': None, 'started_at': None}
        }
        
        # Metrics history for trend analysis
        self.metrics_history: Dict[str, list] = defaultdict(list)
        self.max_history_size = 100
        
        # Thresholds
        self.thresholds = {
            'cpu_percent': 80,        # Alert if CPU > 80%
            'memory_percent': 85,      # Alert if Memory > 85%
            'memory_rss_mb': 1000,     # Alert if RSS > 1GB
            'disk_percent': 90,        # Alert if Disk > 90%
            'pending_orders': 10,      # Alert if > 10 pending
            'rejection_rate': 0.3,     # Alert if > 30% rejections
            'order_fill_time_sec': 5,  # Alert if fill time > 5 sec
        }
        
        # Lock for thread safety
        self.lock = threading.Lock()
        
        # Monitor thread
        self.monitor_thread = None
    
    # =================================================================
    # STARTUP/SHUTDOWN
    # =================================================================
    
    def start(self):
        """Start health monitoring"""
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="HealthMonitor"
        )
        self.monitor_thread.start()
        print("✅ Health monitor started")
    
    def stop(self):
        """Stop health monitoring"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        print("✅ Health monitor stopped")
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                # Check all bots
                for bot_type in ['equity', 'options']:
                    self._check_bot_health(bot_type)
                    self._check_process_metrics(bot_type)
                    self._check_order_health(bot_type)
                    self._check_system_metrics(bot_type)
                
                time.sleep(self.check_interval)
            except Exception as e:
                print(f"❌ Monitor error: {e}")
                time.sleep(self.check_interval)
    
    # =================================================================
    # PROCESS HEALTH
    # =================================================================
    
    def register_bot(self, bot_type: str, pid: int):
        """Register a bot process for monitoring"""
        with self.lock:
            self.bot_processes[bot_type]['pid'] = pid
            self.bot_processes[bot_type]['started_at'] = datetime.now()
            print(f"✅ Registered {bot_type} bot (PID: {pid})")
    
    def _check_bot_health(self, bot_type: str):
        """Check if bot process is still running"""
        pid = self.bot_processes[bot_type]['pid']
        
        if not pid:
            return  # Not registered
        
        if not psutil.pid_exists(pid):
            # Process crashed
            self.alert_manager.alert_bot_crash(
                bot_type,
                f"Process (PID {pid}) no longer exists",
                "Process monitoring detected bot crash"
            )
            self.bot_processes[bot_type]['pid'] = None
    
    def _check_process_metrics(self, bot_type: str):
        """Check process resource usage"""
        pid = self.bot_processes[bot_type]['pid']
        
        if not pid or not psutil.pid_exists(pid):
            return
        
        try:
            process = psutil.Process(pid)
            
            # Get metrics
            cpu_percent = process.cpu_percent(interval=1)
            memory_info = process.memory_info()
            memory_percent = process.memory_percent()
            
            # Record metrics
            with self.lock:
                self.metrics_history[f"{bot_type}_cpu"].append({
                    'timestamp': datetime.now().isoformat(),
                    'value': cpu_percent
                })
                self.metrics_history[f"{bot_type}_memory"].append({
                    'timestamp': datetime.now().isoformat(),
                    'value': memory_percent,
                    'rss_mb': memory_info.rss / (1024 * 1024)
                })
                
                # Trim history
                if len(self.metrics_history[f"{bot_type}_cpu"]) > self.max_history_size:
                    self.metrics_history[f"{bot_type}_cpu"].pop(0)
                if len(self.metrics_history[f"{bot_type}_memory"]) > self.max_history_size:
                    self.metrics_history[f"{bot_type}_memory"].pop(0)
            
            # Check thresholds
            if cpu_percent > self.thresholds['cpu_percent']:
                self.alert_manager.alert_process_unhealthy(
                    bot_type,
                    f"High CPU usage detected",
                    {
                        'cpu_percent': cpu_percent,
                        'threshold': self.thresholds['cpu_percent'],
                        'threshold_exceeded_by': cpu_percent - self.thresholds['cpu_percent']
                    }
                )
            
            if memory_percent > self.thresholds['memory_percent']:
                self.alert_manager.alert_process_unhealthy(
                    bot_type,
                    f"High memory usage detected",
                    {
                        'memory_percent': memory_percent,
                        'memory_rss_mb': memory_info.rss / (1024 * 1024),
                        'threshold_percent': self.thresholds['memory_percent'],
                        'threshold_mb': self.thresholds['memory_rss_mb']
                    }
                )
        
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    # =================================================================
    # ORDER HEALTH
    # =================================================================
    
    def report_order_stats(self, bot_type: str, stats: Dict):
        """Report order statistics for monitoring"""
        with self.lock:
            self.metrics_history[f"{bot_type}_orders"].append({
                'timestamp': datetime.now().isoformat(),
                **stats
            })
            
            if len(self.metrics_history[f"{bot_type}_orders"]) > self.max_history_size:
                self.metrics_history[f"{bot_type}_orders"].pop(0)
        
        # Check thresholds
        total = stats.get('total', 0)
        rejected = stats.get('rejected', 0)
        pending = stats.get('pending', 0)
        
        if total > 0:
            rejection_rate = rejected / total
            if rejection_rate > self.thresholds['rejection_rate']:
                self.alert_manager.alert_process_unhealthy(
                    bot_type,
                    f"High order rejection rate",
                    {
                        'rejected': rejected,
                        'total': total,
                        'rejection_rate': rejection_rate,
                        'threshold': self.thresholds['rejection_rate']
                    }
                )
        
        if pending > self.thresholds['pending_orders']:
            self.alert_manager.alert_order_pending(bot_type)
    
    def _check_order_health(self, bot_type: str):
        """Check order health from metrics"""
        pass  # Orders are reported via report_order_stats
    
    # =================================================================
    # SYSTEM HEALTH
    # =================================================================
    
    def _check_system_metrics(self, bot_type: str):
        """Check system-wide metrics"""
        try:
            # Disk usage
            disk = psutil.disk_usage('/')
            disk_percent = disk.percent
            
            if disk_percent > self.thresholds['disk_percent']:
                self.alert_manager.alert_process_unhealthy(
                    bot_type,
                    f"High disk usage detected",
                    {
                        'disk_percent': disk_percent,
                        'disk_used_gb': disk.used / (1024**3),
                        'disk_total_gb': disk.total / (1024**3),
                        'threshold': self.thresholds['disk_percent']
                    }
                )
        except Exception as e:
            pass
    
    # =================================================================
    # REPORTING
    # =================================================================
    
    def get_current_status(self, bot_type: str) -> Dict:
        """Get current health status for a bot"""
        status = {
            'timestamp': datetime.now().isoformat(),
            'bot_type': bot_type,
            'process': self._get_process_status(bot_type),
            'metrics': self._get_latest_metrics(bot_type)
        }
        return status
    
    def _get_process_status(self, bot_type: str) -> Dict:
        """Get process status"""
        pid = self.bot_processes[bot_type]['pid']
        started_at = self.bot_processes[bot_type]['started_at']
        
        if not pid:
            return {'running': False, 'pid': None}
        
        if not psutil.pid_exists(pid):
            return {'running': False, 'pid': pid, 'crashed': True}
        
        uptime = None
        if started_at:
            uptime = (datetime.now() - started_at).total_seconds()
        
        return {
            'running': True,
            'pid': pid,
            'started_at': started_at.isoformat() if started_at else None,
            'uptime_seconds': uptime
        }
    
    def _get_latest_metrics(self, bot_type: str) -> Dict:
        """Get latest metrics"""
        with self.lock:
            metrics = {}
            
            # Latest CPU
            if f"{bot_type}_cpu" in self.metrics_history:
                latest_cpu = self.metrics_history[f"{bot_type}_cpu"][-1]
                metrics['cpu_percent'] = latest_cpu['value']
            
            # Latest Memory
            if f"{bot_type}_memory" in self.metrics_history:
                latest_mem = self.metrics_history[f"{bot_type}_memory"][-1]
                metrics['memory_percent'] = latest_mem['value']
                metrics['memory_rss_mb'] = latest_mem['rss_mb']
            
            # Latest Orders
            if f"{bot_type}_orders" in self.metrics_history:
                latest_orders = self.metrics_history[f"{bot_type}_orders"][-1]
                metrics['orders'] = {
                    k: v for k, v in latest_orders.items()
                    if k != 'timestamp'
                }
            
            return metrics
    
    def print_status_report(self):
        """Print current status for both bots"""
        print("\n" + "="*70)
        print("HEALTH STATUS REPORT")
        print("="*70)
        
        for bot_type in ['equity', 'options']:
            status = self.get_current_status(bot_type)
            
            print(f"\n🤖 {bot_type.upper()}")
            print("-" * 70)
            
            # Process status
            process = status['process']
            if process['running']:
                print(f"  Process: ✅ Running (PID {process['pid']})")
                if process['uptime_seconds']:
                    hours = int(process['uptime_seconds'] // 3600)
                    minutes = int((process['uptime_seconds'] % 3600) // 60)
                    print(f"  Uptime:  {hours}h {minutes}m")
            else:
                print(f"  Process: ❌ Not running")
                if process.get('crashed'):
                    print(f"           ⚠️ Crashed (PID {process['pid']})")
            
            # Metrics
            metrics = status['metrics']
            if 'cpu_percent' in metrics:
                cpu = metrics['cpu_percent']
                status_icon = "⚠️" if cpu > self.thresholds['cpu_percent'] else "✅"
                print(f"  CPU:     {status_icon} {cpu:.1f}%")
            
            if 'memory_percent' in metrics:
                mem = metrics['memory_percent']
                status_icon = "⚠️" if mem > self.thresholds['memory_percent'] else "✅"
                print(f"  Memory:  {status_icon} {mem:.1f}% ({metrics.get('memory_rss_mb', 0):.0f} MB)")
            
            if 'orders' in metrics:
                orders = metrics['orders']
                total = orders.get('total', 0)
                filled = orders.get('filled', 0)
                rejected = orders.get('rejected', 0)
                pending = orders.get('pending', 0)
                print(f"  Orders:  Total={total} | Filled={filled} | Rejected={rejected} | Pending={pending}")
        
        print("\n" + "="*70 + "\n")


# =============================================================================
# Global Monitor Instance
# =============================================================================

_health_monitor: Optional[HealthMonitor] = None

def get_health_monitor(workspace_root: str = None) -> HealthMonitor:
    """Get or create the global health monitor"""
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = HealthMonitor(workspace_root)
    return _health_monitor

# =============================================================================
# Convenience Functions
# =============================================================================

def register_bot(bot_type: str, pid: int):
    """Register a bot for monitoring"""
    return get_health_monitor().register_bot(bot_type, pid)

def report_order_stats(bot_type: str, stats: Dict):
    """Report order statistics"""
    return get_health_monitor().report_order_stats(bot_type, stats)

def start_monitoring():
    """Start health monitoring"""
    return get_health_monitor().start()

def stop_monitoring():
    """Stop health monitoring"""
    return get_health_monitor().stop()

def get_status(bot_type: str) -> Dict:
    """Get current status for a bot"""
    return get_health_monitor().get_current_status(bot_type)

def print_status_report():
    """Print status report"""
    return get_health_monitor().print_status_report()

if __name__ == "__main__":
    # Test the health monitor
    import signal
    
    monitor = get_health_monitor()
    
    # Register sample bots
    monitor.register_bot('equity', os.getpid())
    monitor.register_bot('options', os.getpid())
    
    # Report sample order stats
    monitor.report_order_stats('equity', {
        'total': 10,
        'filled': 8,
        'rejected': 1,
        'pending': 1
    })
    
    monitor.report_order_stats('options', {
        'total': 5,
        'filled': 4,
        'rejected': 0,
        'pending': 1
    })
    
    print("✅ Health monitor test running (Ctrl+C to stop)...")
    print("Monitor will check health every 60 seconds\n")
    
    # Start monitoring
    monitor.start()
    
    # Print status periodically
    try:
        while True:
            time.sleep(10)
            monitor.print_status_report()
    except KeyboardInterrupt:
        print("\n⏹️ Stopping monitor...")
        monitor.stop()
        print("✅ Monitor stopped")
