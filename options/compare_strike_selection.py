#!/usr/bin/env python3
"""
Comparison: OLD vs NEW Strike Selection

Shows the difference between:
1. OLD: Always selecting ATM (At-The-Money) strike
2. NEW: Always selecting NEAREST strike to alert price
"""

from optcode.strike_deriver import StrikeDeriver

def show_comparison():
    """Show side-by-side comparison of old vs new behavior"""
    
    print("\n" + "="*100)
    print("  🔄 STRIKE SELECTION: OLD (ATM) vs NEW (NEAREST TO ALERT PRICE)")
    print("="*100 + "\n")
    
    test_cases = [
        ("BAJAJFINSV", 2045.10),  # Alert between 2000 and 2100
        ("INFY", 3250.50),         # Alert between 3200 and 3300
        ("TCS", 3850.75),          # Alert between 3800 and 3900
        ("RELIANCE", 1285.40),     # Alert between 1200 and 1300
        ("CAMS", 3887.45),         # Alert between 3800 and 3900
    ]
    
    deriver = StrikeDeriver()
    
    for symbol, alert_price in test_cases:
        print(f"📊 {symbol:15s} | Alert Price: ₹{alert_price:.2f}")
        print("-" * 100)
        
        strike_step = deriver.get_strike_step(symbol).value
        lower_strike = int(alert_price / strike_step) * strike_step
        upper_strike = lower_strike + strike_step
        
        # Old behavior: round() to nearest
        old_atm = round(alert_price / strike_step) * strike_step
        
        # New behavior: get_atm_strike() returns nearest
        nearest = deriver.get_atm_strike(alert_price, symbol)
        
        print(f"  Strike Step: ₹{strike_step:.0f}")
        print(f"  Strikes: {lower_strike:.0f} ←→ {upper_strike:.0f}")
        
        if old_atm == nearest:
            print(f"  🔶 SAME: {old_atm:.0f} (using nearest approach)")
        else:
            print(f"  ✅ CHANGED: {old_atm:.0f} → {nearest:.0f}")
        
        # Show distance from alert
        distance_to_nearest = abs(alert_price - nearest)
        print(f"  Distance from alert to selected strike: ₹{distance_to_nearest:.2f}")
        print()

def show_strike_ladder():
    """Show how strikes are arranged around nearest for buying"""
    
    print("\n" + "="*100)
    print("  📊 STRIKE LADDER: How Calls & Puts Are Arranged Around NEAREST Strike")
    print("="*100 + "\n")
    
    deriver = StrikeDeriver()
    
    symbol = "BAJAJFINSV"
    alert_price = 2045.10
    
    result = deriver.derive_strikes_for_alert(
        symbol=symbol,
        alert_price=alert_price,
        target_contracts=3
    )
    
    print(f"Symbol: {symbol}")
    print(f"Alert Price: ₹{alert_price}")
    print(f"Nearest Strike: ₹{result['nearest_strike']}")
    print()
    
    print("CALL OPTIONS (CE) - For BUY Signal (Bullish):")
    print("-" * 100)
    for i, call in enumerate(result['calls'], 1):
        marker = "← PRIMARY" if call['type'] == "NEAREST" else ""
        print(f"  {i}. Strike ₹{call['strike']:7.0f} ({call['type']:7s}) | "
              f"Distance from alert: {call['distance_from_alert']:+6.2f} {marker}")
    
    print()
    print("PUT OPTIONS (PE) - For SELL Signal (Bearish):")
    print("-" * 100)
    for i, put in enumerate(result['puts'], 1):
        marker = "← PRIMARY" if put['type'] == "NEAREST" else ""
        print(f"  {i}. Strike ₹{put['strike']:7.0f} ({put['type']:7s}) | "
              f"Distance from alert: {put['distance_from_alert']:+6.2f} {marker}")

def show_real_world_examples():
    """Show real-world trading examples"""
    
    print("\n" + "="*100)
    print("  🎯 REAL-WORLD TRADING EXAMPLES")
    print("="*100 + "\n")
    
    from optcode.strike_deriver import AlertStrikeMapper
    
    mapper = AlertStrikeMapper()
    
    scenarios = [
        {
            'name': 'BAJAJFINSV BUY Alert (Bullish)',
            'symbol': 'BAJAJFINSV',
            'price': 2045.10,
            'signal': 'BUY'
        },
        {
            'name': 'INFY SELL Alert (Bearish)',
            'symbol': 'INFY',
            'price': 3250.50,
            'signal': 'SELL'
        },
        {
            'name': 'TCS BUY Alert (Strong Bullish)',
            'symbol': 'TCS',
            'price': 3850.75,
            'signal': 'BUY'
        },
    ]
    
    for scenario in scenarios:
        print(f"📌 {scenario['name']}")
        print("-" * 100)
        
        result = mapper.process_alert(
            symbol=scenario['symbol'],
            price=scenario['price'],
            signal=scenario['signal']
        )
        
        strikes = result['strikes']
        print(f"   Symbol: {scenario['symbol']} | Price: ₹{scenario['price']}")
        print(f"   Nearest Strike: ₹{strikes['nearest_strike']}")
        print(f"   Expiry: {strikes['expiry']}")
        print()
        
        # Show recommended option
        primary = result['recommended']['primary']
        if primary:
            print(f"   ✅ PRIMARY TRADE RECOMMENDATION:")
            print(f"      Symbol: {primary['symbol']}")
            print(f"      Strike: ₹{primary['strike']}")
            print(f"      Type: {primary['type']} ({'Call' if primary['type']=='CE' else 'Put'})")
        
        print()

def main():
    """Run all comparisons"""
    print("\n" + "╔" + "="*98 + "╗")
    print("║" + " "*98 + "║")
    print("║" + "  🔄 STRIKE SELECTION UPDATE: NEAREST TO ALERT PRICE".center(98) + "║")
    print("║" + " "*98 + "║")
    print("╚" + "="*98 + "╝")
    
    show_comparison()
    show_strike_ladder()
    show_real_world_examples()
    
    print("\n" + "="*100)
    print("  ✅ KEY CHANGE: Strikes Now Always Centered on NEAREST Strike to Alert Price")
    print("="*100 + "\n")
    
    print("""
SUMMARY OF CHANGES:

OLD BEHAVIOR (ATM-based):
  - Calculated ATM strike using round()
  - Built ladder around ATM
  - Often created asymmetric strikes relative to alert price
  
NEW BEHAVIOR (NEAREST-based):
  - Finds NEAREST strike to alert price
  - Builds symmetric ladder around nearest strike
  - Gives traders better options on both sides
  
BENEFITS:
  ✅ More accurate strike selection
  ✅ Better leverage on both call and put sides
  ✅ Reduces distance to primary trading strike
  ✅ More flexibility for straddles/strangles
  ✅ Better Greeks matching for alert price
""")

if __name__ == "__main__":
    main()
