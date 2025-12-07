#!/usr/bin/env python3
"""
Demo: Strike Selection with InstrumentManager

Shows how to use the instrument manager and strike selector for options trading.
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from optcode.instrument_manager import get_instrument_manager
from optcode.strike_selector import (
    select_atm_strike,
    select_strike_by_delta,
    list_all_strikes,
    get_strike_token,
    instrument_stats
)


def demo_instrument_loading():
    """Demo 1: Load and verify instrument master"""
    print("\n" + "="*70)
    print("  DEMO 1: INSTRUMENT LOADING")
    print("="*70)
    
    mgr = get_instrument_manager()
    
    stats = mgr.get_stats()
    print(f"\n✅ Instrument Manager Stats:")
    for key, value in stats.items():
        print(f"   {key}: {value}")


def demo_strike_selection_reliance():
    """Demo 2: Select strikes for RELIANCE"""
    print("\n" + "="*70)
    print("  DEMO 2: STRIKE SELECTION - RELIANCE")
    print("="*70)
    
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        print("❌ Instrument manager not loaded")
        return
    
    # Check available expirations for RELIANCE
    reliance_strikes = mgr.get_strikes_for_underlying("RELIANCE")
    
    if not reliance_strikes:
        print("❌ No RELIANCE strikes found in database")
        return
    
    # Get unique expirations
    expirations = sorted(set(s.get('expiry') for s in reliance_strikes))
    
    print(f"\n✅ Found {len(reliance_strikes)} RELIANCE contracts")
    print(f"   Available expirations: {expirations[:3]}")  # Show first 3
    
    if expirations:
        expiry = expirations[0]
        print(f"\n📊 Analyzing expiry: {expiry}")
        
        # List all strikes
        all_strikes = list_all_strikes("RELIANCE", expiry)
        print(f"   CE Strikes: {len(all_strikes['CE'])}")
        if all_strikes['CE']:
            ce_strikes = [float(s.get('strike', 0)) for s in all_strikes['CE']]
            print(f"      Range: ₹{min(ce_strikes):.0f} - ₹{max(ce_strikes):.0f}")
        
        print(f"   PE Strikes: {len(all_strikes['PE'])}")
        if all_strikes['PE']:
            pe_strikes = [float(s.get('strike', 0)) for s in all_strikes['PE']]
            print(f"      Range: ₹{min(pe_strikes):.0f} - ₹{max(pe_strikes):.0f}")
        
        # Select ATM strike
        if all_strikes['CE']:
            atm = select_atm_strike("RELIANCE", expiry, "CE", spot_price=2850)
            if atm:
                print(f"\n🎯 ATM Strike (CE) @ ₹2850:")
                print(f"   Symbol: {atm['symbol']}")
                print(f"   Strike: ₹{atm['strike']}")
                print(f"   Token: {atm['token']}")
                print(f"   Expiry: {atm['expiry']}")


def demo_strike_selection_infy():
    """Demo 3: Select strikes for INFY"""
    print("\n" + "="*70)
    print("  DEMO 3: STRIKE SELECTION - INFY")
    print("="*70)
    
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        print("❌ Instrument manager not loaded")
        return
    
    infy_strikes = mgr.get_strikes_for_underlying("INFY")
    
    if not infy_strikes:
        print("⚠️  No INFY strikes found (INFY may not be in F&O)")
        return
    
    expirations = sorted(set(s.get('expiry') for s in infy_strikes))
    
    print(f"\n✅ Found {len(infy_strikes)} INFY contracts")
    print(f"   Available expirations: {expirations[:3] if expirations else 'None'}")
    
    if expirations:
        expiry = expirations[0]
        all_strikes = list_all_strikes("INFY", expiry)
        print(f"   {expiry}: {len(all_strikes['CE'])} CE + {len(all_strikes['PE'])} PE strikes")


def demo_new_stocks():
    """Demo 4: Show newly added stocks"""
    print("\n" + "="*70)
    print("  DEMO 4: NEWLY AVAILABLE F&O STOCKS")
    print("="*70)
    
    mgr = get_instrument_manager()
    
    # Get unique F&O stock names
    fo_stocks = set()
    for inst in mgr.instruments:
        if inst.get('exch_seg') == 'NFO' and inst.get('instrumenttype') in ('OPTSTK', 'FUTSTK'):
            name = inst.get('name')
            if name:
                fo_stocks.add(name)
    
    fo_stocks = sorted(list(fo_stocks))
    
    print(f"\n✅ Total F&O Stocks: {len(fo_stocks)}")
    
    # Show a sample of stocks
    print(f"\n📋 Sample stocks (first 20):")
    for stock in fo_stocks[:20]:
        print(f"   {stock}")
    
    # Highlight recently added (stocks that weren't in old database)
    old_stocks = {
        "RELIANCE", "INFY", "TCS", "BAJAJ-AUTO", "BANKBARODA", "BANKNIFTY",
        "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT",
        "GAIL", "GRASIM", "HCLTECH", "HDFC", "HDFCBANK", "HEROMOTOCO",
        "HINDALCO", "HINDUNILVR", "ICICIBANK", "IDFCFIRSTB", "INDIGO",
        "JSWSTEEL", "KOTAKBANK", "LT", "MARUTI", "MCDOHLCONS", "MINDTREE",
        "MOTHERSON", "NESTLEIND", "NMDC", "NTPC", "ONGC", "PHARMEASY",
        "POWERGRID", "RELINFRA", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
        "TATASTEEL", "TECHM", "TITAN", "TORNTPHARM", "UPL", "WIPRO",
    }
    
    new_stocks = [s for s in fo_stocks if s not in old_stocks]
    
    if new_stocks:
        print(f"\n🆕 Newly Added Stocks ({len(new_stocks)}):")
        for stock in new_stocks[:15]:
            print(f"   {stock}")


def demo_symbol_lookup():
    """Demo 5: Direct symbol lookup"""
    print("\n" + "="*70)
    print("  DEMO 5: DIRECT SYMBOL LOOKUP")
    print("="*70)
    
    mgr = get_instrument_manager()
    
    # Example: Look up a specific symbol
    test_symbol = "RELIANCE30DEC251600CE"
    
    contract = mgr.get_strike_by_symbol(test_symbol)
    
    if contract:
        print(f"\n✅ Found contract: {test_symbol}")
        print(f"   Name: {contract.get('name')}")
        print(f"   Strike: ₹{contract.get('strike')}")
        print(f"   Expiry: {contract.get('expiry')}")
        print(f"   Token: {contract.get('token')}")
    else:
        print(f"\n⚠️  Symbol not found: {test_symbol}")
        print(f"   (This is expected if RELIANCE 1600 CE doesn't exist in this expiry)")


def main():
    """Run all demos"""
    print("\n" + "🤖 OPTIONS TRADING - STRIKE SELECTION DEMO")
    print("="*70)
    
    try:
        demo_instrument_loading()
        demo_strike_selection_reliance()
        demo_strike_selection_infy()
        demo_new_stocks()
        demo_symbol_lookup()
        
        print("\n" + "="*70)
        print("  ✅ ALL DEMOS COMPLETED")
        print("="*70 + "\n")
        
    except Exception as e:
        print(f"\n❌ Error during demo: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
