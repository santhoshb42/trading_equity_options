#!/usr/bin/env python3
"""
Test script to fetch candle details for 5 symbols from live market
"""

import sys
import os
from datetime import datetime, timedelta
import json

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from candle_fetcher import CandleFetcher
from api import SmartAPIWrapper  # Our API wrapper

def test_live_candles():
    """Test fetching candles for 5 symbols from live market"""
    
    print("\n" + "="*70)
    print("🔄 LIVE CANDLE FETCHING TEST")
    print("="*70 + "\n")
    
    # Initialize API wrapper
    print("📡 Initializing SmartAPI connection...")
    try:
        api = SmartAPIWrapper()
        print("✅ SmartAPI initialized\n")
    except Exception as e:
        print(f"❌ Failed to initialize SmartAPI: {e}")
        return False
    
    # Test symbols with tokens
    test_symbols = {
        "RELIANCE": "3045",
        "SBIN": "4119",
        "INFY": "4963",
        "TCS": "3789",
        "HDFC": "1333"
    }
    
    print("📊 Fetching candles for 5 symbols:\n")
    
    # Initialize candle fetcher
    fetcher = CandleFetcher(api.smart_api, cache_ttl_seconds=60)
    
    results = {}
    success_count = 0
    
    # Current time for date range
    to_date = datetime.now()
    from_date = to_date - timedelta(hours=1)  # Last 1 hour
    
    from_date_str = from_date.strftime("%Y-%m-%d %H:%M")
    to_date_str = to_date.strftime("%Y-%m-%d %H:%M")
    
    print(f"📅 Date Range: {from_date_str} to {to_date_str}")
    print(f"⏱️  Interval: FIVE_MINUTE\n")
    
    for symbol, token in test_symbols.items():
        print(f"Fetching {symbol:12} (Token: {token:5})...", end=" ")
        
        try:
            df = fetcher.fetch_candles(
                exchange="NSE",
                token=token,
                interval="FIVE_MINUTE",
                from_date=from_date_str,
                to_date=to_date_str,
                use_cache=False  # Don't use cache for this test
            )
            
            if df is not None and len(df) > 0:
                print(f"✅ Got {len(df)} candles")
                
                # Display first and last candle
                first = df.iloc[0]
                last = df.iloc[-1]
                
                results[symbol] = {
                    "status": "success",
                    "candles_count": len(df),
                    "first_time": str(first.get('timestamp', first.get('time', 'N/A'))),
                    "first_price": f"O: {first['open']:.2f}, H: {first['high']:.2f}, L: {first['low']:.2f}, C: {first['close']:.2f}",
                    "last_time": str(last.get('timestamp', last.get('time', 'N/A'))),
                    "last_price": f"O: {last['open']:.2f}, H: {last['high']:.2f}, L: {last['low']:.2f}, C: {last['close']:.2f}",
                    "volume": f"First: {first.get('volume', 0)}, Last: {last.get('volume', 0)}"
                }
                
                print(f"  └─ First: {results[symbol]['first_price']}")
                print(f"  └─ Last:  {results[symbol]['last_price']}")
                
                success_count += 1
            else:
                print(f"⚠️  No candles returned (market may be closed)")
                results[symbol] = {
                    "status": "no_data",
                    "candles_count": 0
                }
        
        except Exception as e:
            print(f"❌ Error: {str(e)[:50]}")
            results[symbol] = {
                "status": "error",
                "error": str(e)
            }
        
        print()
    
    # Summary
    print("="*70)
    print("📈 SUMMARY")
    print("="*70)
    print(f"✅ Successfully fetched: {success_count}/5 symbols")
    print(f"❌ Failed: {5 - success_count}/5 symbols\n")
    
    # Detailed results
    print("📋 DETAILED RESULTS:\n")
    for symbol, result in results.items():
        status = result.get("status", "unknown")
        print(f"{symbol}:")
        print(f"  Status: {status}")
        if status == "success":
            print(f"  Candles: {result['candles_count']}")
            print(f"  First: {result['first_price']}")
            print(f"  Last: {result['last_price']}")
        elif status == "no_data":
            print(f"  Reason: Market may be closed or no data available")
        else:
            print(f"  Error: {result.get('error', 'Unknown error')}")
        print()
    
    # Return status
    if success_count >= 3:
        print("✅ RESULT: Candle fetching is WORKING (fetched from 3+ symbols)")
        return True
    elif success_count > 0:
        print("⚠️  RESULT: Partial success (some symbols fetched, market may be closed)")
        return True
    else:
        print("❌ RESULT: Failed to fetch candles from any symbol")
        return False

if __name__ == "__main__":
    try:
        success = test_live_candles()
        exit_code = 0 if success else 1
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    
    print("\n" + "="*70)
    sys.exit(exit_code)
