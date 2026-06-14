#!/usr/bin/env python3
"""
INDESTRUCTIBLE BOT STATE RECOVERY SYSTEM
Automatically saves and restores bot state to survive crashes and reboots
"""

import os
import json
import time
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

class StateManager:
    """Manages persistent state for crash recovery"""
    
    def __init__(self, state_dir="/var/lib/trading-bot"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        # State files
        self.positions_file = self.state_dir / "positions.json"
        self.orders_file = self.state_dir / "orders.json"
        self.config_file = self.state_dir / "config.json"
        self.heartbeat_file = self.state_dir / "heartbeat.json"
        
        # SQLite database for detailed logging
        self.db_file = self.state_dir / "trading_bot.db"
        self._init_database()
        
        # Auto-save thread
        self._stop_event = threading.Event()
        self._save_thread = None
        self.start_auto_save()
    
    def _init_database(self):
        """Initialize SQLite database for state tracking"""
        with sqlite3.connect(self.db_file) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL,
                    sl_price REAL,
                    target_price REAL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    order_id TEXT,
                    pnl REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    response TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS bot_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    state_type TEXT NOT NULL,
                    state_data TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS heartbeat (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    uptime_seconds INTEGER,
                    positions_count INTEGER,
                    orders_count INTEGER,
                    memory_mb REAL,
                    cpu_percent REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
                CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);
                CREATE INDEX IF NOT EXISTS idx_bot_state_type ON bot_state(state_type);
                CREATE INDEX IF NOT EXISTS idx_heartbeat_timestamp ON heartbeat(timestamp);
            """)
    
    def save_position(self, position_data: Dict):
        """Save position to both JSON and database"""
        # Save to JSON for quick access
        positions = self.load_positions()
        positions[position_data['symbol']] = position_data
        
        with open(self.positions_file, 'w') as f:
            json.dump(positions, f, indent=2, default=str)
        
        # Save to database for detailed tracking
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO positions 
                (symbol, action, quantity, entry_price, current_price, sl_price, 
                 target_price, status, timestamp, order_id, pnl, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position_data['symbol'],
                position_data['action'],
                position_data['quantity'],
                position_data['entry_price'],
                position_data.get('current_price'),
                position_data.get('sl_price'),
                position_data.get('target_price'),
                position_data['status'],
                position_data['timestamp'],
                position_data.get('order_id'),
                position_data.get('pnl', 0),
                datetime.now().isoformat()
            ))
    
    def load_positions(self) -> Dict:
        """Load positions from JSON file"""
        if self.positions_file.exists():
            try:
                with open(self.positions_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading positions: {e}")
        return {}
    
    def save_order(self, order_data: Dict):
        """Save order to both JSON and database"""
        # Save to JSON
        orders = self.load_orders()
        orders[order_data['order_id']] = order_data
        
        with open(self.orders_file, 'w') as f:
            json.dump(orders, f, indent=2, default=str)
        
        # Save to database
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO orders 
                (order_id, symbol, action, quantity, price, status, timestamp, response, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_data['order_id'],
                order_data['symbol'],
                order_data['action'],
                order_data['quantity'],
                order_data['price'],
                order_data['status'],
                order_data['timestamp'],
                json.dumps(order_data.get('response', {})),
                datetime.now().isoformat()
            ))
    
    def load_orders(self) -> Dict:
        """Load orders from JSON file"""
        if self.orders_file.exists():
            try:
                with open(self.orders_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading orders: {e}")
        return {}
    
    def save_config(self, config_data: Dict):
        """Save configuration state"""
        with open(self.config_file, 'w') as f:
            json.dump(config_data, f, indent=2)
    
    def load_config(self) -> Dict:
        """Load configuration state"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}")
        return {}
    
    def save_heartbeat(self, uptime_seconds=0, positions_count=0, orders_count=0, 
                      memory_mb=0, cpu_percent=0):
        """Save heartbeat to track bot health"""
        heartbeat_data = {
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': uptime_seconds,
            'positions_count': positions_count,
            'orders_count': orders_count,
            'memory_mb': memory_mb,
            'cpu_percent': cpu_percent,
            'pid': os.getpid()
        }
        
        # Save to JSON for quick access
        with open(self.heartbeat_file, 'w') as f:
            json.dump(heartbeat_data, f, indent=2)
        
        # Save to database for historical tracking
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                INSERT INTO heartbeat 
                (timestamp, uptime_seconds, positions_count, orders_count, memory_mb, cpu_percent)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                heartbeat_data['timestamp'],
                uptime_seconds,
                positions_count,
                orders_count,
                memory_mb,
                cpu_percent
            ))
    
    def get_last_heartbeat(self) -> Optional[Dict]:
        """Get the last heartbeat data"""
        if self.heartbeat_file.exists():
            try:
                with open(self.heartbeat_file, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return None
    
    def is_crash_recovery_needed(self) -> bool:
        """Check if bot crashed and needs recovery"""
        last_heartbeat = self.get_last_heartbeat()
        if not last_heartbeat:
            return False
        
        last_time = datetime.fromisoformat(last_heartbeat['timestamp'])
        time_diff = datetime.now() - last_time
        
        # If last heartbeat was more than 2 minutes ago, consider it a crash
        return time_diff > timedelta(minutes=2)
    
    def recover_from_crash(self):
        """Recover bot state after a crash"""
        print("🔄 CRASH RECOVERY: Restoring bot state...")
        
        # Load saved positions
        positions = self.load_positions()
        orders = self.load_orders()
        config = self.load_config()
        
        recovery_info = {
            'positions_recovered': len(positions),
            'orders_recovered': len(orders),
            'recovery_timestamp': datetime.now().isoformat(),
            'was_crash_recovery': True
        }
        
        print(f"✅ RECOVERY COMPLETE:")
        print(f"   📊 Positions recovered: {len(positions)}")
        print(f"   📋 Orders recovered: {len(orders)}")
        print(f"   ⚙️ Config restored: {'Yes' if config else 'No'}")
        
        # Save recovery info to state
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                INSERT INTO bot_state (state_type, state_data, timestamp)
                VALUES (?, ?, ?)
            """, ('crash_recovery', json.dumps(recovery_info), datetime.now().isoformat()))
        
        return positions, orders, config
    
    def validate_recovered_positions(self, positions: Dict, broker):
        """🔧 FIX GAP-004: Validate recovered positions against actual broker state
        
        Ensures that recovered positions actually exist on the broker.
        If a position doesn't exist on broker, it's marked as invalid.
        """
        validated_positions = {}
        invalid_positions = []
        
        for symbol, position_data in positions.items():
            try:
                # Query broker for actual position
                broker_position = broker.get_position(symbol)
                
                if not broker_position:
                    # Position doesn't exist on broker
                    print(f"⚠️ VALIDATION FAILED: {symbol} recovered but not found on broker")
                    invalid_positions.append({
                        'symbol': symbol,
                        'reason': 'Not found on broker',
                        'position_data': position_data
                    })
                    continue
                
                # Verify quantity matches
                recovered_qty = position_data.get('quantity', 0)
                broker_qty = broker_position.get('quantity', 0)
                
                if recovered_qty != broker_qty:
                    print(f"⚠️ MISMATCH: {symbol} - Recovered qty: {recovered_qty}, Broker qty: {broker_qty}")
                    invalid_positions.append({
                        'symbol': symbol,
                        'reason': f'Quantity mismatch (recovered: {recovered_qty}, broker: {broker_qty})',
                        'position_data': position_data,
                        'broker_position': broker_position
                    })
                    continue
                
                # Position is valid
                validated_positions[symbol] = position_data
                print(f"✅ VALIDATION PASSED: {symbol} ({recovered_qty} units)")
                
            except Exception as e:
                print(f"⚠️ VALIDATION ERROR for {symbol}: {e}")
                invalid_positions.append({
                    'symbol': symbol,
                    'reason': f'Validation error: {str(e)}',
                    'position_data': position_data
                })
        
        if invalid_positions:
            print(f"\n❌ RECOVERY ISSUE: {len(invalid_positions)} position(s) failed validation")
            for invalid in invalid_positions:
                print(f"   - {invalid['symbol']}: {invalid['reason']}")
            
            # Log invalid positions for manual review
            with sqlite3.connect(self.db_file) as conn:
                conn.execute("""
                    INSERT INTO bot_state (state_type, state_data, timestamp)
                    VALUES (?, ?, ?)
                """, ('invalid_positions', json.dumps(invalid_positions), datetime.now().isoformat()))
        
        return validated_positions, invalid_positions
    
    def start_auto_save(self):
        """Start automatic state saving in background"""
        if self._save_thread and self._save_thread.is_alive():
            return
        
        def auto_save_loop():
            import psutil
            start_time = time.time()
            
            while not self._stop_event.is_set():
                try:
                    # Get system metrics
                    process = psutil.Process(os.getpid())
                    memory_mb = process.memory_info().rss / 1024 / 1024
                    cpu_percent = process.cpu_percent()
                    uptime_seconds = int(time.time() - start_time)
                    
                    # Get counts from memory (would be from actual bot state)
                    positions = self.load_positions()
                    orders = self.load_orders()
                    
                    # Save heartbeat
                    self.save_heartbeat(
                        uptime_seconds=uptime_seconds,
                        positions_count=len(positions),
                        orders_count=len(orders),
                        memory_mb=memory_mb,
                        cpu_percent=cpu_percent
                    )
                    
                    # Wait 30 seconds before next save
                    self._stop_event.wait(30)
                    
                except Exception as e:
                    print(f"Error in auto-save: {e}")
                    self._stop_event.wait(30)
        
        self._save_thread = threading.Thread(target=auto_save_loop, daemon=True)
        self._save_thread.start()
        print("✅ Auto-save heartbeat started")
    
    def stop_auto_save(self):
        """Stop automatic state saving"""
        self._stop_event.set()
        if self._save_thread:
            self._save_thread.join(timeout=5)
    
    def cleanup_old_data(self, days_to_keep=30):
        """Clean up old data to prevent database bloat"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_str = cutoff_date.isoformat()
        
        with sqlite3.connect(self.db_file) as conn:
            # Keep positions but clean old heartbeats and state
            conn.execute("DELETE FROM heartbeat WHERE created_at < ?", (cutoff_str,))
            conn.execute("DELETE FROM bot_state WHERE created_at < ?", (cutoff_str,))
            
            # Clean completed orders older than 7 days
            order_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            conn.execute("""
                DELETE FROM orders 
                WHERE created_at < ? AND status IN ('COMPLETED', 'CANCELLED', 'REJECTED')
            """, (order_cutoff,))
    
    def get_recovery_stats(self) -> Dict:
        """Get statistics about bot crashes and recoveries"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) as recovery_count,
                       MAX(timestamp) as last_recovery
                FROM bot_state 
                WHERE state_type = 'crash_recovery'
            """)
            result = cursor.fetchone()
            
            return {
                'total_recoveries': result[0] if result else 0,
                'last_recovery': result[1] if result and result[1] else None,
                'database_size_mb': os.path.getsize(self.db_file) / 1024 / 1024 if self.db_file.exists() else 0
            }

# Global state manager instance
state_manager = StateManager()

def ensure_indestructible_startup():
    """Initialize indestructible bot with state recovery"""
    print("🛡️ INITIALIZING INDESTRUCTIBLE BOT...")
    
    # Check if crash recovery is needed
    if state_manager.is_crash_recovery_needed():
        print("⚠️ CRASH DETECTED - Initiating recovery...")
        positions, orders, config = state_manager.recover_from_crash()
        return positions, orders, config
    else:
        print("✅ Normal startup - no crash recovery needed")
        return {}, {}, {}

if __name__ == "__main__":
    # Test the state manager
    print("🧪 Testing State Manager...")
    
    # Test crash recovery
    if state_manager.is_crash_recovery_needed():
        state_manager.recover_from_crash()
    
    # Test saving data
    test_position = {
        'symbol': 'TEST-EQ',
        'action': 'BUY',
        'quantity': 10,
        'entry_price': 100.0,
        'status': 'OPEN',
        'timestamp': datetime.now().isoformat()
    }
    
    state_manager.save_position(test_position)
    
    # Test loading data
    positions = state_manager.load_positions()
    print(f"✅ Loaded {len(positions)} positions")
    
    # Start auto-save
    state_manager.start_auto_save()
    
    print("✅ State Manager test completed")
    
    # Show recovery stats
    stats = state_manager.get_recovery_stats()
    print(f"📊 Recovery Stats: {stats}")