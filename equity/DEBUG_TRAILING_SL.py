#!/usr/bin/env python3
"""
Debug script to check why modify_order logs aren't appearing.

This script verifies all conditions required for trailing SL modification.
"""

import json
import os
from datetime import datetime
from pathlib import Path

def check_environment_variables():
    """Check if TRAIL_SL_ENABLED is set."""
    print("=" * 80)
    print("1. CHECKING ENVIRONMENT VARIABLES")
    print("=" * 80)
    
    trail_sl_enabled = os.getenv("TRAIL_SL_ENABLED", "True")
    print(f"   TRAIL_SL_ENABLED = {trail_sl_enabled}")
    print(f"   Is Enabled? {trail_sl_enabled.lower() == 'true'}")
    
    if trail_sl_enabled.lower() != "true":
        print("\n   ⚠️  ISSUE FOUND: Trailing SL is DISABLED!")
        print("      Fix: export TRAIL_SL_ENABLED=True")
    else:
        print("\n   ✅ Trailing SL is enabled")
    print()

def check_positions_file():
    """Check positions.json for SL order details."""
    print("=" * 80)
    print("2. CHECKING POSITIONS.JSON")
    print("=" * 80)
    
    positions_file = Path("/root/santhosh/trading/equity/data/positions.json")
    
    if not positions_file.exists():
        print("   ⚠️  No positions.json found!")
        return
    
    with open(positions_file, 'r') as f:
        positions_data = json.load(f)
    
    # Handle both dict and list formats
    if isinstance(positions_data, dict):
        positions = list(positions_data.values())
    else:
        positions = positions_data
    
    if not positions:
        print("   ⚠️  No positions in positions.json - nothing to trail!")
        return
    
    print(f"\n   Found {len(positions)} position(s):")
    
    for i, pos in enumerate(positions, 1):
        print(f"\n   Position {i}: {pos.get('symbol', 'UNKNOWN')}")
        print(f"   ├─ Status: {pos.get('status', 'UNKNOWN')}")
        print(f"   ├─ Action: {pos.get('action', 'UNKNOWN')}")
        print(f"   ├─ Entry Price: ₹{pos.get('entry_price', 0):.2f}")
        
        # Current LTP check
        ltp = pos.get('last_ltp')
        entry_price = pos.get('entry_price', 0)
        if ltp and entry_price > 0:
            profit = ((ltp - entry_price) / entry_price) * 100
            print(f"   ├─ Current LTP: ₹{ltp:.2f}")
            print(f"   ├─ Profit: {profit:+.2f}%")
        else:
            print(f"   ├─ Current LTP: {ltp} (❌ Missing - can't trail without this!)")
        
        # SL Order check
        sl_order_id = pos.get('sl_order_id')
        if sl_order_id:
            print(f"   ├─ SL Order ID: {sl_order_id} ✅")
            print(f"   ├─ Current SL: ₹{pos.get('sl_price', 0):.2f}")
        else:
            print(f"   ├─ SL Order ID: None (❌ No SL order - can't modify what doesn't exist!)")
        
        # Trail activation check
        trail_activated = pos.get('trail_activated', False)
        print(f"   ├─ Trail Activated: {trail_activated}")
        
        # Product type check
        sl_order_product = pos.get('sl_order_product')
        if sl_order_product:
            print(f"   ├─ SL Order Product: {sl_order_product} ✅")
        else:
            print(f"   ├─ SL Order Product: None (⚠️  May prevent modify_order)")
    
    print()

def check_recent_logs():
    """Check recent logs for trailing SL messages."""
    print("=" * 80)
    print("3. CHECKING RECENT LOGS")
    print("=" * 80)
    
    logs_dir = Path("/root/santhosh/trading/logs")
    
    if not logs_dir.exists():
        print("   ⚠️  No logs directory found!")
        return
    
    # Find most recent log file
    log_files = sorted(logs_dir.glob("**/monitor*.log"), reverse=True)
    
    if not log_files:
        print("   ⚠️  No monitor log files found!")
        return
    
    latest_log = log_files[0]
    print(f"\n   Checking: {latest_log.name}")
    print(f"   Last modified: {datetime.fromtimestamp(latest_log.stat().st_mtime)}")
    
    # Search for trail-related logs
    trail_keywords = [
        "TRAIL_DEBUG",
        "TRAIL_ACTIVATED",
        "TRAIL_SL_CALCULATION",
        "TRAIL_SL_STEPPED",
        "TRAIL_SL_MODIFIED",
        "TRAIL_SL_MODIFY_FAILED",
        "TRAIL_SL_SKIP"
    ]
    
    with open(latest_log, 'r') as f:
        content = f.read()
    
    found_any = False
    for keyword in trail_keywords:
        if keyword in content:
            found_any = True
            count = content.count(keyword)
            print(f"   ✅ Found {count} occurrence(s) of '{keyword}'")
    
    if not found_any:
        print("   ❌ No trailing SL messages found in logs!")
        print("\n   Possible causes:")
        print("      • TRAIL_SL_ENABLED is False")
        print("      • No positions with status='OPEN'")
        print("      • No positions with action='BUY'")
        print("      • Monitor not running long enough to accumulate logs")
    
    print()

def generate_debugging_script():
    """Generate a script to add verbose logging."""
    print("=" * 80)
    print("4. RECOMMENDED DEBUGGING STEPS")
    print("=" * 80)
    
    print("""
   To enable MAXIMUM visibility into trailing SL execution:

   STEP 1: Ensure TRAIL_SL_ENABLED is true
   $ export TRAIL_SL_ENABLED=True
   
   STEP 2: Check if your positions have SL orders
   $ python3 << 'PYEOF'
   import json
   with open('equity/data/positions.json') as f:
       positions = json.load(f)
   for pos in positions:
       print(f"{pos['symbol']}: SL Order ID = {pos.get('sl_order_id')}, Product = {pos.get('sl_order_product')}")
   PYEOF
   
   STEP 3: Look for these log messages (in order):
   • TRAIL_DEBUG          → Confirms position is being checked
   • TRAIL_ACTIVATED      → Trailing has started
   • TRAIL_SL_CALCULATION → New SL being calculated
   • ADAPTIVE_SL_CALCULATED → Adaptive buffer applied
   • TRAIL_SL_STEPPED     → Moving to next step
   • TRAIL_SL_MODIFIED    → Order successfully modified ✅
   
   STEP 4: If you see TRAIL_DEBUG but not TRAIL_ACTIVATED:
   • Your position profit is NEGATIVE
   • The check: current_ltp < entry_price * 0.99
   • Means: Position is down more than 1%
   
   STEP 5: If you see TRAIL_ACTIVATED but not TRAIL_SL_CALCULATION:
   • Already processed this profit step
   • Check: current_step <= last_executed_step
   • Means: Waiting for next 0.5% profit milestone
   
   STEP 6: If you see TRAIL_SL_CALCULATION but not TRAIL_SL_STEPPED:
   • New SL is not better than current SL
   • Check: new_trail_sl <= current_sl
   • Means: SL already at or above calculated level
   
   STEP 7: If you see TRAIL_SL_STEPPED but not TRAIL_SL_MODIFIED:
   • modify_order() call failed
   • Check TRAIL_SL_MODIFY_FAILED for error details
   • May be: Rate limit, broker error, or network issue

    """)
    print()

if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "TRAILING SL DEBUGGING TOOL".center(78) + "║")
    print("║" + "Find out why modify_order logs aren't appearing".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")
    print()
    
    check_environment_variables()
    check_positions_file()
    check_recent_logs()
    generate_debugging_script()
    
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("""
   If TRAIL_DEBUG logs appear:
   ✅ Your trailing SL system is running
   └─ Follow the message sequence to find where the flow stops
   
   If NO TRAIL_DEBUG logs appear:
   ❌ Check TRAIL_SL_ENABLED and position status
   └─ See STEP 1 and STEP 2 above
    """)
    print()
