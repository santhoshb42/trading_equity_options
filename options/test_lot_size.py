#!/usr/bin/env python3
"""
Test Lot Size Fix - Verify orders are placed with correct lot sizes

Tests:
1. Instrument manager loads lot sizes correctly
2. get_lot_size() returns correct values for various symbols
3. Falls back to default 1 for missing symbols
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from optcode.instrument_manager import get_instrument_manager
from optcode.optlogging import logger

def test_instrument_manager_loads():
    """Test that instrument manager loads data"""
    print("\n" + "="*70)
    print("TEST 1: Instrument Manager Loads Data")
    print("="*70)
    
    mgr = get_instrument_manager()
    stats = mgr.get_stats()
    
    print(f"✅ Loaded {stats['total_instruments']} instruments")
    print(f"✅ Indexed {stats['symbols_indexed']} symbols")
    print(f"✅ Indexed {stats['tokens_indexed']} tokens")
    print(f"✅ Found {stats['fo_stocks']} F&O stocks")
    
    assert stats['is_loaded'], "Instrument manager should be loaded"
    assert stats['total_instruments'] > 0, "Should have instruments"
    return mgr

def test_lot_size_retrieval(mgr):
    """Test getting lot sizes for known symbols"""
    print("\n" + "="*70)
    print("TEST 2: Get Lot Sizes for Known Symbols")
    print("="*70)
    
    test_cases = [
        ("JIOFIN30DEC25325CE", 2350),
        ("IREDA24FEB26140CE", 3450),
        ("HDFCAMC30DEC252300PE", 300),
        ("MCX30DEC2510500CE", 125),
        ("MOTHERSON27JAN26101CE", 6150),
    ]
    
    for symbol, expected_lotsize in test_cases:
        actual_lotsize = mgr.get_lot_size(symbol)
        status = "✅" if actual_lotsize == expected_lotsize else "❌"
        print(f"{status} {symbol:30s} | Expected: {expected_lotsize:6d} | Got: {actual_lotsize:6d}")
        assert actual_lotsize == expected_lotsize, f"Lot size mismatch for {symbol}"
    
    print("\n✅ All lot sizes retrieved correctly!")

def test_fallback_to_default(mgr):
    """Test fallback to default lot size for missing symbols"""
    print("\n" + "="*70)
    print("TEST 3: Fallback to Default for Missing Symbols")
    print("="*70)
    
    # Try to get lot size for a symbol that doesn't exist
    fake_symbol = "NONEXISTENT30DEC25100CE"
    lotsize = mgr.get_lot_size(fake_symbol)
    
    print(f"Requested lot size for non-existent symbol: {fake_symbol}")
    print(f"Got: {lotsize} (should be default of 1)")
    
    assert lotsize == 1, "Should return default lot size of 1 for missing symbol"
    print("✅ Correctly falls back to default!")

def test_order_placement_simulation():
    """Simulate order placement with lot sizes"""
    print("\n" + "="*70)
    print("TEST 4: Order Placement Simulation")
    print("="*70)
    
    mgr = get_instrument_manager()
    
    # Simulate different trade scenarios
    trades = [
        {
            'symbol': 'JIOFIN30DEC25325CE',
            'entry_price': 125.50,
            'contracts': 1,
        },
        {
            'symbol': 'HDFCAMC30DEC252300PE',
            'entry_price': 2350.00,
            'contracts': 2,
        },
        {
            'symbol': 'MOTHERSON27JAN26101CE',
            'entry_price': 45.25,
            'contracts': 1,
        },
    ]
    
    print("\nSimulating orders with correct lot sizes:")
    print("-" * 70)
    
    for trade in trades:
        symbol = trade['symbol']
        entry_price = trade['entry_price']
        num_contracts = trade['contracts']
        
        lot_size = mgr.get_lot_size(symbol)
        total_quantity = num_contracts * lot_size
        total_value = total_quantity * entry_price
        
        print(f"\nSymbol:           {symbol}")
        print(f"  Entry Price:    ₹{entry_price:.2f}")
        print(f"  Lot Size:       {lot_size:,d} shares/contract")
        print(f"  # Contracts:    {num_contracts}")
        print(f"  Total Quantity: {total_quantity:,d} shares")
        print(f"  Total Value:    ₹{total_value:,.2f}")
    
    print("\n" + "-" * 70)
    print("✅ Order placement simulation complete!")

if __name__ == '__main__':
    try:
        print("\n╔" + "="*68 + "╗")
        print("║" + " "*68 + "║")
        print("║" + "OPTIONS BOT - LOT SIZE FIX VERIFICATION".center(68) + "║")
        print("║" + " "*68 + "║")
        print("╚" + "="*68 + "╝")
        
        mgr = test_instrument_manager_loads()
        test_lot_size_retrieval(mgr)
        test_fallback_to_default(mgr)
        test_order_placement_simulation()
        
        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED!")
        print("="*70)
        print("\nSummary:")
        print("  ✅ Instrument manager loads correctly")
        print("  ✅ Lot sizes retrieved for all symbols")
        print("  ✅ Fallback to default works")
        print("  ✅ Order placement simulation successful")
        print("\n🎉 Lot size fix is working correctly!")
        print("="*70 + "\n")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
