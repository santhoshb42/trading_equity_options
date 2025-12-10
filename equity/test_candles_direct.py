#!/usr/bin/env python3
"""
Test script to fetch candle details for 5 symbols from live market
Using SmartAPI directly without import conflicts
"""

import sys
import os
from datetime import datetime, timedelta
import json

def test_live_candles():
    """Test fetching candles for 5 symbols from live market"""
    
    print("\n" + "="*70)
    print("🔄 LIVE CANDLE FETCHING TEST")
    print("="*70 + "\n")
    
    # Import SmartAPI
    try:
        from smartapi import SmartConnect
        import pyotp
        print("✅ SmartAPI libraries imported\n")
    except ImportError as e:
        print(f"❌ Failed to import SmartAPI: {e}")
        return False
    
    # Load credentials from config
    try:
        # Try to load from environment or config file
        import configparser
        config = configparser.ConfigParser()
        
        config_paths = [
            "/root/santhosh/trading/equity/eqcode/config.py",
            "/root/santhosh/trading/equity/eqcode/.env",
            os.path.expanduser("~/.trading_config")
        ]
        
        # For now, try to get credentials from environment
        api_key = os.getenv("SMARTAPI_KEY")
        client_code = os.getenv("SMARTAPI_CLIENT_CODE")
        password = os.getenv("SMARTAPI_PASSWORD")
        totp = os.getenv("SMARTAPI_TOTP")
        
        if not all([api_key, client_code, password]):
            print("⚠️  Missing credentials. Checking if API is already initialized...\n")
        
        print(f"📡 Attempting SmartAPI connection...")
        
        # Try to connect
        sac = SmartConnect(api_key=api_key or "demo_key")
        
        # If credentials are available, authenticate
        if all([api_key, client_code, password, totp]):
            totp_obj = pyotp.TOTP(totp)
            totp_token = totp_obj.now()
            
            session = sac.generateSession(client_code, password, totp_token)
            
            if not session or not session.get("status"):
                print(f"❌ Authentication failed: {session}")
                return False
            
            print("✅ SmartAPI authenticated\n")
        else:
            print("⚠️  Credentials not available - testing with demo connection\n")
    
    except Exception as e:
        print(f"⚠️  Could not initialize SmartAPI: {e}")
        print("Testing with alternative approach...\n")
    
    # Test symbols with tokens
    test_symbols = {
        "RELIANCE": "3045",
        "SBIN": "4119",
        "INFY": "4963",
        "TCS": "3789",
        "HDFC": "1333"
    }
    
    print("📊 Testing candle data for 5 symbols:\n")
    
    # Try to fetch using the historical API
    results = {}
    success_count = 0
    
    # Current time for date range
    to_date = datetime.now()
    from_date = to_date - timedelta(hours=2)
    
    from_date_str = from_date.strftime("%Y-%m-%d %H:%M")
    to_date_str = to_date.strftime("%Y-%m-%d %H:%M")
    
    print(f"📅 Date Range: {from_date_str} to {to_date_str}")
    print(f"⏱️  Interval: FIVE_MINUTE\n")
    
    # Prepare historical API request parameters
    for symbol, token in test_symbols.items():
        print(f"Testing {symbol:12} (Token: {token:5})...", end=" ", flush=True)
        
        try:
            # Historical candle data parameters
            params = {
                "mode": "FULL",
                "exchangeTokens": {
                    "NSE": [token]
                },
                "interval": "FIVE_MINUTE",
                "fromDate": from_date_str,
                "toDate": to_date_str
            }
            
            # Check if SmartAPI has the getCandleData method
            if hasattr(sac, 'getCandleData'):
                resp = sac.getCandleData(params)
                
                if resp and resp.get("status"):
                    data = resp.get("data", {})
                    
                    if data and "candles" in data:
                        candles = data["candles"]
                        if isinstance(candles, list) and len(candles) > 0:
                            print(f"✅ Got {len(candles)} candles")
                            
                            # Display first and last candle
                            first = candles[0]
                            last = candles[-1]
                            
                            results[symbol] = {
                                "status": "success",
                                "candles_count": len(candles),
                                "first": f"[{first[1]:.2f}, {first[2]:.2f}, {first[3]:.2f}, {first[4]:.2f}]",
                                "last": f"[{last[1]:.2f}, {last[2]:.2f}, {last[3]:.2f}, {last[4]:.2f}]",
                            }
                            
                            print(f"  └─ First: O:{first[1]:.2f} H:{first[2]:.2f} L:{first[3]:.2f} C:{first[4]:.2f}")
                            print(f"  └─ Last:  O:{last[1]:.2f} H:{last[2]:.2f} L:{last[3]:.2f} C:{last[4]:.2f}")
                            
                            success_count += 1
                        else:
                            print(f"⚠️  No candles in response")
                            results[symbol] = {"status": "no_data"}
                    else:
                        print(f"⚠️  No data in response")
                        results[symbol] = {"status": "no_data"}
                else:
                    print(f"⚠️  API returned error")
                    results[symbol] = {"status": "api_error"}
            else:
                print(f"⚠️  getCandleData not available")
                results[symbol] = {"status": "method_unavailable"}
        
        except Exception as e:
            error_msg = str(e)[:40]
            print(f"❌ {error_msg}")
            results[symbol] = {
                "status": "error",
                "error": error_msg
            }
        
        print()
    
    # Summary
    print("="*70)
    print("📈 TEST SUMMARY")
    print("="*70)
    print(f"✅ Successfully fetched: {success_count}/5 symbols")
    print(f"⚠️  Failed/No data: {5 - success_count}/5 symbols\n")
    
    if success_count >= 3:
        print("✅ RESULT: Candle fetching is WORKING")
        return True
    elif success_count > 0:
        print("⚠️  RESULT: Partial success (market may be closed or API limits)")
        return True
    else:
        print("❌ RESULT: Could not fetch candles (API may not be authenticated)")
        return False

if __name__ == "__main__":
    try:
        success = test_live_candles()
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        success = False
    
    print("\n" + "="*70 + "\n")
    sys.exit(0 if success else 1)
