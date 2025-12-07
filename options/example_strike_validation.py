#!/usr/bin/env python3
"""
Practical Example: Strike Validation in Action

Shows real-world usage of the Strike Validation System with actual broker data.
"""

import sys
from pathlib import Path

# Add options module to path
sys.path.insert(0, str(Path(__file__).parent))

from optcode.strike_validator import StrikeValidator
from optcode.strike_deriver import AlertStrikeMapper


def example_1_validate_existing_strike():
    """Validate a strike that EXISTS in broker data"""
    print("\n" + "="*80)
    print("EXAMPLE 1: Validating an EXISTING Strike")
    print("="*80)
    
    validator = StrikeValidator()
    
    # BANKNIFTY 30-DEC-2025 @ 40000 CE (likely exists in broker data)
    print("\n📊 Checking: BANKNIFTY30DEC2540000CE")
    print("   (This strike should exist based on available data)")
    
    is_valid, token, details = validator.validate_strike(
        symbol="BANKNIFTY",
        expiry="2025-12-30",
        strike=40000,
        contract_type="CE"
    )
    
    if is_valid:
        print(f"\n✅ VALID STRIKE")
        print(f"   Symbol: {details['symbol']}")
        print(f"   Token: {token}")
        print(f"   Lot Size: {details['lot_size']}")
        print(f"   Tick Size: {details['tick_size']}")
        print(f"   Ready to place order!")
    else:
        print(f"\n❌ INVALID STRIKE")
        print(f"   Reason: {details['reason']}")
        
        # Show what's available
        if 'nearest_available_strike' in details:
            nearest = details['nearest_available_strike']
            print(f"\n   Nearest available: {nearest['symbol']}")
            print(f"   Distance from requested: {nearest['distance']}")


def example_2_validate_nonexistent_strike():
    """Validate a strike that DOESN'T exist"""
    print("\n" + "="*80)
    print("EXAMPLE 2: Validating a NON-EXISTENT Strike")
    print("="*80)
    
    validator = StrikeValidator()
    
    # Invalid: 99999 (unlikely to exist)
    print("\n📊 Checking: BANKNIFTY30DEC2599999CE")
    print("   (This strike probably doesn't exist)")
    
    is_valid, token, details = validator.validate_strike(
        symbol="BANKNIFTY",
        expiry="2025-12-30",
        strike=99999,
        contract_type="CE"
    )
    
    if is_valid:
        print(f"✅ Valid (unexpected!)")
    else:
        print(f"\n❌ INVALID - Strike not found")
        print(f"   Reason: {details['reason']}")
        
        # Provide diagnostics
        if 'strike_range' in details:
            r = details['strike_range']
            print(f"\n   Available Range:")
            print(f"      Min: {r['min']:.0f}")
            print(f"      Max: {r['max']:.0f}")
            print(f"      Requested: {r['requested']:.0f} (OUT OF RANGE)")
        
        if 'nearest_available_strike' in details:
            nearest = details['nearest_available_strike']
            print(f"\n   Nearest Available:")
            print(f"      Symbol: {nearest['symbol']}")
            print(f"      Strike: {nearest['strike']}")
            print(f"      Distance: {nearest['distance']}")


def example_3_discover_available_strikes():
    """Discover what strikes are actually available"""
    print("\n" + "="*80)
    print("EXAMPLE 3: Discovering Available Strikes")
    print("="*80)
    
    validator = StrikeValidator()
    
    # Get available BANKNIFTY strikes
    print("\n📊 Finding all BANKNIFTY CE strikes for 30-DEC-2025")
    
    available = validator.get_available_strikes(
        symbol="BANKNIFTY",
        expiry="2025-12-30",
        contract_type="CE"
    )
    
    if available:
        print(f"\n✅ Found {len(available)} available CE strikes")
        
        # Show range
        strikes = [c['strike'] for c in available]
        print(f"\n   Strike Range: {min(strikes):.0f} - {max(strikes):.0f}")
        
        # Show sample
        print(f"\n   Sample strikes (first 10):")
        for i, strike_info in enumerate(available[:10]):
            print(f"      {i+1}. {strike_info['symbol']:25s} (Strike: {strike_info['strike']:10.0f})")
        
        # Show sample at end
        print(f"\n   Sample strikes (last 5):")
        for i, strike_info in enumerate(available[-5:], start=len(available)-4):
            print(f"      {i}. {strike_info['symbol']:25s} (Strike: {strike_info['strike']:10.0f})")
    else:
        print(f"\n❌ No strikes found")


def example_4_validate_multiple_strikes():
    """Validate multiple strikes from derivation"""
    print("\n" + "="*80)
    print("EXAMPLE 4: Validating Multiple Derived Strikes")
    print("="*80)
    
    validator = StrikeValidator()
    
    # Simulate derived strikes from algorithm
    symbol = "BANKNIFTY"
    expiry = "2025-12-30"
    derived_strikes = [39500, 40000, 40500]  # From derivation algorithm
    
    print(f"\n📊 Derived strikes for {symbol}:")
    print(f"   {derived_strikes}")
    print(f"   Expiry: {expiry}")
    
    # Validate all
    results = validator.validate_multiple_strikes(
        symbol=symbol,
        expiry=expiry,
        strikes=derived_strikes,
        contract_type="CE"
    )
    
    print(f"\n   Validation Results:")
    valid_count = 0
    for strike, (is_valid, token, details) in sorted(results.items()):
        status = "✅" if is_valid else "❌"
        symbol_str = details.get('symbol', 'UNKNOWN')
        
        if is_valid:
            valid_count += 1
            print(f"      {status} {strike:8.0f} → {symbol_str:25s} (Token: {token})")
        else:
            print(f"      {status} {strike:8.0f} → {symbol_str:25s} (NOT FOUND)")
    
    print(f"\n   Summary: {valid_count}/{len(derived_strikes)} valid")
    
    if valid_count == len(derived_strikes):
        print(f"   ✅ All derived strikes are tradable!")
    else:
        print(f"   ⚠️  Some strikes invalid - will need to use alternatives")


def example_5_alert_to_order_flow():
    """Complete flow: Alert → Derivation → Validation → Ready to Order"""
    print("\n" + "="*80)
    print("EXAMPLE 5: Complete Alert → Order Flow")
    print("="*80)
    
    # Create mapper with validation enabled
    mapper = AlertStrikeMapper(validate_strikes=True)
    
    # Simulate incoming alert
    alert = {
        'symbol': 'BANKNIFTY',
        'price': 40250.50,
        'signal': 'BUY',
        'confidence': 85
    }
    
    print(f"\n📊 Incoming Alert:")
    print(f"   Symbol: {alert['symbol']}")
    print(f"   Price: ₹{alert['price']}")
    print(f"   Signal: {alert['signal']}")
    print(f"   Confidence: {alert['confidence']}%")
    
    # Process alert (with validation)
    print(f"\n🔄 Processing alert (with validation)...")
    result = mapper.process_alert(
        symbol=alert['symbol'],
        price=alert['price'],
        signal=alert['signal'],
        expiry="2025-12-30",
        target_contracts=3
    )
    
    # Check validation results
    validation = result['validation']
    print(f"\n   ✅ Validation Results:")
    print(f"      Enabled: {validation['enabled']}")
    print(f"      Valid Strikes: {validation['valid_count']}/{len(result['option_symbols'])}")
    print(f"      All Valid: {validation['all_valid']}")
    
    # Show recommended strikes
    print(f"\n   📋 Recommended Strikes:")
    for i, opt in enumerate(result['option_symbols'], 1):
        status = "✅" if opt['valid'] else "❌"
        token = f" (Token: {opt['token']})" if opt['valid'] else ""
        moneyness = opt['moneyness']
        print(f"      {i}. {status} {opt['symbol']:25s} [{moneyness:3s}]{token}")
    
    # Ready for order placement?
    if validation['all_valid']:
        print(f"\n   🚀 READY TO PLACE ORDERS!")
        print(f"      All {validation['valid_count']} strikes validated")
        print(f"      Tokens retrieved from broker")
        print(f"      Can proceed with order placement")
    else:
        print(f"\n   ⚠️  ATTENTION NEEDED")
        print(f"      {validation['invalid_count']} strike(s) not available in broker system")
        print(f"      Using only {validation['valid_count']} valid strike(s)")


def example_6_check_strike_before_order():
    """Before placing order: Final check that strike still exists"""
    print("\n" + "="*80)
    print("EXAMPLE 6: Pre-Order Strike Verification")
    print("="*80)
    
    validator = StrikeValidator()
    
    # Before placing order, final check
    symbol_to_trade = "BANKNIFTY30DEC2540000CE"
    
    print(f"\n📊 Before placing order, verifying strike exists...")
    print(f"   Symbol: {symbol_to_trade}")
    
    # Quick check
    is_valid, token, details = validator.validate_strike(
        symbol="BANKNIFTY",
        expiry="2025-12-30",
        strike=40000,
        contract_type="CE"
    )
    
    if is_valid:
        print(f"\n   ✅ VERIFIED - Strike exists")
        print(f"      Symbol: {details['symbol']}")
        print(f"      Token: {token}")
        print(f"      Status: Ready for order placement")
        
        print(f"\n   📝 Order Details:")
        print(f"      Symbol: {symbol_to_trade}")
        print(f"      Action: BUY")
        print(f"      Quantity: 1 lot (40 contracts)")
        print(f"      Price: Best available")
        print(f"      Token: {token}")
        
        print(f"\n   ✅ All checks passed - Place order!")
    else:
        print(f"\n   ❌ FAILED - Strike no longer available")
        print(f"      Reason: {details['reason']}")
        print(f"      Action: Use alternative strike or cancel order")


def main():
    """Run all examples"""
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*78 + "║")
    print("║" + "STRIKE VALIDATION - PRACTICAL EXAMPLES".center(78) + "║")
    print("║" + " "*78 + "║")
    print("╚" + "="*78 + "╝")
    
    try:
        # Run examples
        example_1_validate_existing_strike()
        example_2_validate_nonexistent_strike()
        example_3_discover_available_strikes()
        example_4_validate_multiple_strikes()
        example_5_alert_to_order_flow()
        example_6_check_strike_before_order()
        
        # Summary
        print("\n" + "="*80)
        print("✅ ALL EXAMPLES COMPLETED")
        print("="*80)
        print("\n📌 KEY TAKEAWAYS:")
        print("   1. ✅ Always validate strikes before placing orders")
        print("   2. ✅ Use validator to discover what's actually available")
        print("   3. ✅ Validate multiple strikes efficiently")
        print("   4. ✅ Check strike status right before order placement")
        print("   5. ✅ Use diagnostics to find alternatives if needed")
        print("   6. ✅ Tokens needed for order placement are from validator")
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
