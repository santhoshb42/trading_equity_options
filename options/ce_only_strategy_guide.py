#!/usr/bin/env python3
"""
Quick Reference: CE-Only Trading Strategy

This script demonstrates the simplified CE-only approach where:
- All alerts trade Call Options (CE)
- No Put Options (PE) are ever used
- Strategy focuses on directional calls with varying strikes
"""

from optcode.strike_deriver import AlertStrikeMapper, StrikeDeriver

def demonstrate_ce_only_strategy():
    """Demonstrate CE-only trading across different scenarios"""
    
    print("\n" + "="*100)
    print("  📋 CE-ONLY TRADING STRATEGY REFERENCE")
    print("="*100 + "\n")
    
    mapper = AlertStrikeMapper()
    deriver = StrikeDeriver()
    
    scenarios = [
        {
            'symbol': 'BAJAJFINSV',
            'price': 2045.10,
            'signal': 'BUY',
            'description': 'Strong Bullish Signal'
        },
        {
            'symbol': 'INFY',
            'price': 3250.50,
            'signal': 'SELL',
            'description': 'Bearish Signal (Still trades CE!)'
        },
        {
            'symbol': 'TCS',
            'price': 3850.75,
            'signal': 'HOLD',
            'description': 'Neutral Signal'
        },
        {
            'symbol': 'RELIANCE',
            'price': 1285.40,
            'signal': 'BUY',
            'description': 'Breakout Signal'
        },
    ]
    
    for idx, scenario in enumerate(scenarios, 1):
        print(f"\n{'='*100}")
        print(f"  SCENARIO {idx}: {scenario['description']}")
        print(f"{'='*100}")
        
        result = mapper.process_alert(
            symbol=scenario['symbol'],
            price=scenario['price'],
            signal=scenario['signal'],
            target_contracts=3
        )
        
        alert = result['alert']
        strikes = result['strikes']
        options = result['option_symbols']
        
        print(f"\n📊 ALERT DETAILS")
        print("-" * 100)
        print(f"  Symbol: {alert['symbol']}")
        print(f"  Price: ₹{alert['price']}")
        print(f"  Signal: {alert['signal']}")
        
        print(f"\n📈 STRIKE DERIVATION")
        print("-" * 100)
        print(f"  Alert Price: ₹{strikes['alert_price']}")
        print(f"  Nearest Strike: ₹{strikes['nearest_strike']}")
        print(f"  Expiry: {strikes['expiry']} ({strikes['days_to_expiry']} days)")
        print(f"  Strike Step: ₹{strikes['strike_step']}")
        
        print(f"\n🎯 AVAILABLE CALL OPTIONS (CE ONLY)")
        print("-" * 100)
        for i, opt in enumerate(options, 1):
            primary_marker = " ← PRIMARY" if opt['position_type'] == 'NEAREST' else ""
            print(f"  {i}. {opt['symbol']:20s} Strike: ₹{opt['strike']:7.0f}  "
                  f"Type: {opt['position_type']:7s}{primary_marker}")
        
        print(f"\n✅ RECOMMENDED TRADE")
        print("-" * 100)
        primary = result['recommended']['primary']
        if primary:
            print(f"  Symbol: {primary['symbol']}")
            print(f"  Strike: ₹{primary['strike']}")
            print(f"  Type: {primary['type']} (Call Option)")
            print(f"  Action: BUY 1 LOT at MARKET")
            
            # Calculate some quick metrics
            entry_strike = primary['strike']
            alert_price = scenario['price']
            distance = abs(entry_strike - alert_price)
            pct = (distance / alert_price) * 100
            print(f"  Distance from Alert Price: ₹{distance:.2f} ({pct:.2f}%)")

def show_strike_ladder_comparison():
    """Show strike ladder for different symbols"""
    
    print("\n\n" + "="*100)
    print("  📊 CE-ONLY STRIKE LADDERS FOR DIFFERENT SYMBOLS")
    print("="*100 + "\n")
    
    mapper = AlertStrikeMapper()
    
    test_cases = [
        ("BAJAJFINSV", 2045.10, "Low-mid price stock"),
        ("INFY", 3250.50, "High price stock"),
        ("RELIANCE", 1285.40, "Mid price stock"),
    ]
    
    for symbol, price, description in test_cases:
        print(f"\n{symbol:15s} ({description}) | Alert Price: ₹{price}")
        print("-" * 100)
        
        result = mapper.process_alert(
            symbol=symbol,
            price=price,
            signal="BUY",
            target_contracts=5
        )
        
        print("CE Strike Ladder (All Calls):")
        for i, opt in enumerate(result['option_symbols'], 1):
            marker = "← PRIMARY (NEAREST)" if opt['position_type'] == 'NEAREST' else ""
            print(f"  {i}. {opt['symbol']:20s} ₹{opt['strike']:7.0f} {marker}")

def show_strategy_rules():
    """Show the CE-only strategy rules"""
    
    print("\n\n" + "="*100)
    print("  📋 CE-ONLY STRATEGY RULES")
    print("="*100 + "\n")
    
    rules = [
        ("Rule 1", "Always Trade Calls (CE)", 
         "All alerts convert to Call options. Put options (PE) are never used."),
        
        ("Rule 2", "Select NEAREST Strike",
         "The strike closest to alert price is the primary trading strike."),
        
        ("Rule 3", "Build Strike Ladder",
         "Primary strike + OTM strikes above it for flexibility."),
        
        ("Rule 4", "Signal-Agnostic",
         "BUY, SELL, or HOLD signals all result in CE trades."),
        
        ("Rule 5", "Directional Clarity",
         "CE-only means always bullish directional bias."),
        
        ("Rule 6", "Simplified Risk Management",
         "No put hedges - focus on profitable calls."),
        
        ("Rule 7", "Consistent Strike Selection",
         "Nearest strike ensures minimum distance to alert price."),
        
        ("Rule 8", "Multiple Expiries Available",
         "Weekly (next Thursday) and Monthly (last Thursday) options available."),
    ]
    
    for rule_num, rule_name, description in rules:
        print(f"{rule_num}: {rule_name}")
        print(f"   └─ {description}\n")

def main():
    """Run all demonstrations"""
    
    print("\n" + "╔" + "="*98 + "╗")
    print("║" + " "*98 + "║")
    print("║" + "  📈 CE-ONLY TRADING STRATEGY - COMPLETE REFERENCE".center(98) + "║")
    print("║" + " "*98 + "║")
    print("╚" + "="*98 + "╝")
    
    demonstrate_ce_only_strategy()
    show_strike_ladder_comparison()
    show_strategy_rules()
    
    print("\n\n" + "="*100)
    print("  ✅ CE-ONLY STRATEGY SUMMARY")
    print("="*100 + "\n")
    
    print("""
KEY PRINCIPLES:

1. 🎯 ALWAYS TRADE CALLS (CE)
   - No Put Options (PE) are ever used
   - All signals (BUY, SELL, HOLD) result in CE trades

2. 📍 NEAREST STRIKE SELECTION
   - Finds strike closest to alert price
   - Minimizes distance between alert and trading strike

3. 📊 CALL STRIKE LADDER
   - Primary: NEAREST strike
   - Secondary: OTM strikes ±1, ±2 steps above

4. ⏰ WEEKLY & MONTHLY EXPIRIES
   - Weekly expiry: Next Thursday
   - Monthly expiry: Last Thursday of month

5. 💰 SIMPLIFIED CAPITAL ALLOCATION
   - All capital directed to profitable calls
   - No split between calls and puts

6. 🔒 DIRECTIONAL CONSISTENCY
   - Always bullish via call options
   - Simpler trade management and Greeks tracking

BENEFITS:

✅ Clear directional strategy
✅ Reduced complexity (CE only)
✅ Better capital efficiency
✅ Simplified Greeks management
✅ Easier profit taking on calls
✅ No hedging confusion
✅ Consistent trader psychology

EXAMPLE TRADES:

• BAJAJFINSV @ ₹2045.10 → BAJAJFINSV25DEC2000CE (NEAREST)
• INFY @ ₹3250.50 → INFY25DEC3300CE (NEAREST)
• TCS @ ₹3850.75 → TCS25DEC3900CE (NEAREST)
• RELIANCE @ ₹1285.40 → RELIANCE25DEC1300CE (NEAREST)

All trades are Call Options (CE). No Put Options (PE) trading.
""")
    
    print("="*100)
    print("  ✅ CE-ONLY STRATEGY IS LIVE AND READY")
    print("="*100 + "\n")

if __name__ == "__main__":
    main()
