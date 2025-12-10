#!/usr/bin/env python3
"""
Test script to fetch candle details for 5 symbols from live market
Using direct SmartAPI approach
"""

import sys
import os
from datetime import datetime, timedelta

print("\n" + "="*70)
print("🔄 LIVE CANDLE FETCHING TEST - 5 SYMBOLS")
print("="*70 + "\n")

try:
    # Check if SmartAPI is available
    from SmartApi import SmartConnect
    import pyotp
    print("✅ SmartAPI libraries available\n")
except ImportError as e:
    print(f"❌ SmartAPI not installed: {e}")
    sys.exit(1)

# Test symbols with Angel One tokens (NSE)
test_symbols = {
    "RELIANCE": "3045",
    "SBIN": "4119",
    "INFY": "4963",
    "TCS": "3789",
    "HDFC": "1333"
}

print("📊 Candle Data Symbols to Test:")
print("=" * 70)
for symbol, token in test_symbols.items():
    print(f"  {symbol:12} → Token: {token}")
print()

# Check credentials
api_key = os.getenv("SMARTAPI_KEY")
client_code = os.getenv("SMARTAPI_CLIENT_CODE")
password = os.getenv("SMARTAPI_PASSWORD")
totp_secret = os.getenv("SMARTAPI_TOTP")

print("🔑 Credential Check:")
print("=" * 70)
print(f"  API Key: {'✅ Present' if api_key else '❌ Missing'}")
print(f"  Client Code: {'✅ Present' if client_code else '❌ Missing'}")
print(f"  Password: {'✅ Present' if password else '❌ Missing'}")
print(f"  TOTP Secret: {'✅ Present' if totp_secret else '❌ Missing'}")
print()

if not all([api_key, client_code, password, totp_secret]):
    print("⚠️  CREDENTIALS NOT COMPLETE")
    print("\n💡 To test with live market, set environment variables:")
    print("   export SMARTAPI_KEY='your_key'")
    print("   export SMARTAPI_CLIENT_CODE='your_code'")
    print("   export SMARTAPI_PASSWORD='your_password'")
    print("   export SMARTAPI_TOTP='your_totp_secret'")
    print()
    print("Proceeding with demo/simulation...\n")
    sys.exit(0)

# Try to authenticate
print("🔐 Authentication Attempt:")
print("=" * 70)

try:
    # Initialize SmartConnect
    sac = SmartConnect(api_key=api_key)
    print(f"  SmartConnect initialized with API key")
    
    # Generate TOTP
    totp = pyotp.TOTP(totp_secret)
    totp_token = totp.now()
    
    # Authenticate
    print(f"  Attempting authentication...")
    session = sac.generateSession(client_code, password, totp_token)
    
    if not session or session.get("status") != True:
        print(f"  ❌ Authentication failed")
        print(f"  Response: {session}")
        sys.exit(1)
    
    auth_token = session.get("data", {}).get("jwtToken")
    print(f"  ✅ Authentication successful")
    print(f"  Token: {auth_token[:20]}..." if auth_token else "  Token: Unknown")
    print()
    
except Exception as e:
    print(f"  ❌ Authentication error: {e}")
    sys.exit(1)

# Fetch candles for 5 symbols
print("📡 Fetching Candles from Live Market:")
print("=" * 70)

to_date = datetime.now()
from_date = to_date - timedelta(hours=2)

from_date_str = from_date.strftime("%Y-%m-%d %H:%M")
to_date_str = to_date.strftime("%Y-%m-%d %H:%M")

print(f"  Date Range: {from_date_str} to {to_date_str}")
print(f"  Interval: FIVE_MINUTE")
print()

success_count = 0
results = {}

for symbol, token in test_symbols.items():
    print(f"  {symbol:12}...", end=" ", flush=True)
    
    try:
        # Prepare request parameters
        params = {
            "mode": "FULL",
            "exchangeTokens": {
                "NSE": [token]
            },
            "interval": "FIVE_MINUTE",
            "fromDate": from_date_str,
            "toDate": to_date_str
        }
        
        # Fetch candles
        resp = sac.getCandleData(params)
        
        if resp and resp.get("status"):
            candles = resp.get("data", {}).get("candles", [])
            
            if candles and len(candles) > 0:
                print(f"✅ {len(candles)} candles")
                
                # Get first and last candle
                first = candles[0]
                last = candles[-1]
                
                # OHLCV format: [timestamp, open, high, low, close, volume]
                results[symbol] = {
                    "count": len(candles),
                    "first": {
                        "time": first[0] if len(first) > 0 else "N/A",
                        "ohlc": f"O:{first[1]:.2f} H:{first[2]:.2f} L:{first[3]:.2f} C:{first[4]:.2f}",
                        "volume": first[5] if len(first) > 5 else 0
                    },
                    "last": {
                        "time": last[0] if len(last) > 0 else "N/A",
                        "ohlc": f"O:{last[1]:.2f} H:{last[2]:.2f} L:{last[3]:.2f} C:{last[4]:.2f}",
                        "volume": last[5] if len(last) > 5 else 0
                    }
                }
                
                success_count += 1
            else:
                print(f"⚠️  No candles (market closed?)")
                results[symbol] = {"count": 0, "status": "no_data"}
        else:
            print(f"❌ API error")
            results[symbol] = {"status": "api_error"}
    
    except Exception as e:
        print(f"❌ {str(e)[:40]}")
        results[symbol] = {"status": "error", "error": str(e)}

print()
print("=" * 70)
print("📈 RESULTS SUMMARY")
print("=" * 70)

for symbol, data in results.items():
    if data.get("count", 0) > 0:
        print(f"\n✅ {symbol}")
        print(f"   Candles: {data['count']}")
        print(f"   First (OHLC): {data['first']['ohlc']} | Vol: {data['first']['volume']}")
        print(f"   Last  (OHLC): {data['last']['ohlc']} | Vol: {data['last']['volume']}")
    else:
        status = data.get("status", "unknown")
        print(f"\n⚠️  {symbol}: {status}")

print()
print("=" * 70)
print(f"OVERALL: {success_count}/5 symbols successfully fetched")

if success_count >= 3:
    print("✅ CANDLE FETCHING IS WORKING")
elif success_count > 0:
    print("⚠️  PARTIAL SUCCESS - Market may be closed or API limiting")
else:
    print("❌ CANDLE FETCHING FAILED")

print("=" * 70 + "\n")

sys.exit(0 if success_count >= 3 else 1)
