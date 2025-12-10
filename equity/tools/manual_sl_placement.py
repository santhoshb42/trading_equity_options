#!/usr/bin/env python3
"""
Manual SL Placement Tool for Emergency Use
Checks broker holdings and places missing SL orders
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eqcode.angelone import AngelOneBroker
from eqcode.bot_logging import log_event

# Initialize broker
broker = AngelOneBroker()

# Positions that need SL orders (from analysis)
POSITIONS_NEEDING_SL = [
    {"symbol": "ASHOKLEY-EQ", "qty": 60, "entry": 161.99, "sl": 161.10},
    {"symbol": "SHRIRAMFIN-EQ", "qty": 11, "entry": 850.60, "sl": 846.30},
    {"symbol": "ESCORTS-EQ", "qty": 2, "entry": 3718.00, "sl": 3699.40},
    {"symbol": "HDFCAMC-EQ", "qty": 3, "entry": 2591.20, "sl": 2578.20},
    {"symbol": "NUVAMA-EQ", "qty": 1, "entry": 7274.50, "sl": 7238.10},
    {"symbol": "PETRONET-EQ", "qty": 35, "entry": 273.40, "sl": 272.00},
]

def main():
    print("=" * 80)
    print("MANUAL SL PLACEMENT TOOL")
    print("=" * 80)
    print()
    
    # Get current holdings from broker
    print("Step 1: Fetching current holdings from broker...")
    try:
        holdings = broker.get_holdings()
        print(f"  ✓ Found {len(holdings)} holdings")
        
        # Create a dict of current holdings
        holdings_dict = {}
        for h in holdings:
            symbol = h.get('tradingsymbol', '') or h.get('symbol', '')
            qty = int(h.get('quantity', 0))
            holdings_dict[symbol] = qty
            print(f"    - {symbol}: {qty} shares")
        print()
    except Exception as e:
        print(f"  ✗ Error fetching holdings: {e}")
        return 1
    
    # Check which positions are still open
    print("Step 2: Checking which positions need SL orders...")
    positions_to_fix = []
    for pos in POSITIONS_NEEDING_SL:
        symbol = pos['symbol']
        expected_qty = pos['qty']
        
        if symbol in holdings_dict:
            actual_qty = holdings_dict[symbol]
            if actual_qty == expected_qty:
                print(f"  ⚠️  {symbol}: OPEN ({actual_qty} shares) - NEEDS SL")
                positions_to_fix.append(pos)
            elif actual_qty > 0:
                print(f"  ⚠️  {symbol}: OPEN but qty mismatch (expected {expected_qty}, got {actual_qty})")
                # Ask user if they want to place SL anyway
                positions_to_fix.append({**pos, "qty": actual_qty})
            else:
                print(f"  ✓ {symbol}: Position closed (qty={actual_qty})")
        else:
            print(f"  ✓ {symbol}: Not in holdings (position closed)")
    
    print()
    
    if not positions_to_fix:
        print("✓ All positions are either closed or have SL orders already")
        return 0
    
    # Confirm with user
    print(f"Found {len(positions_to_fix)} positions that need SL orders:")
    for pos in positions_to_fix:
        print(f"  - {pos['symbol']}: {pos['qty']} shares @ SL {pos['sl']}")
    print()
    
    response = input("Do you want to place SL orders for these positions? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("Aborted by user")
        return 0
    
    # Place SL orders
    print()
    print("Step 3: Placing SL orders...")
    success_count = 0
    failed = []
    
    for pos in positions_to_fix:
        symbol = pos['symbol']
        qty = pos['qty']
        sl_price = pos['sl']
        
        print(f"  Placing SL for {symbol} ({qty} shares @ {sl_price})...")
        try:
            result = broker.place_stoploss_order(
                symbol=symbol,
                quantity=qty,
                sl_trigger_price=sl_price,
                product="INTRADAY"  # Match original order product type
            )
            
            if result and hasattr(result, 'order_id'):
                print(f"    ✓ SL order placed: {result.order_id}")
                success_count += 1
            else:
                print(f"    ✗ Failed: {result}")
                failed.append(symbol)
        except Exception as e:
            print(f"    ✗ Error: {e}")
            failed.append(symbol)
    
    print()
    print("=" * 80)
    print(f"SUMMARY: Placed {success_count}/{len(positions_to_fix)} SL orders")
    if failed:
        print(f"Failed: {', '.join(failed)}")
    else:
        print("All SL orders placed successfully!")
    print("=" * 80)
    
    return 0 if not failed else 1

if __name__ == "__main__":
    sys.exit(main())
