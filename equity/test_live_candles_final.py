#!/usr/bin/env python3
"""
Test script to fetch candle details for 5 symbols from live market
Uses existing bot credentials
"""

import sys
import os
from datetime import datetime, timedelta

print("\n" + "="*70)
print("🔄 LIVE CANDLE FETCHING TEST - 5 SYMBOLS")
print("="*70 + "\n")

try:
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

# Load credentials from environment (same names used by the bot)
api_key = os.getenv("ANGEL_API_KEY")
client_code = os.getenv("ANGEL_CLIENT_CODE")
password = os.getenv("ANGEL_PASSWORD")
totp_secret = os.getenv("ANGEL_TOTP_SECRET")

print("🔑 Credential Check:")
print("=" * 70)
print(f"  ANGEL_API_KEY: {'✅ Present' if api_key else '❌ Missing'}")
print(f"  ANGEL_CLIENT_CODE: {'✅ Present' if client_code else '❌ Missing'}")
print(f"  ANGEL_PASSWORD: {'✅ Present' if password else '❌ Missing'}")
print(f"  ANGEL_TOTP_SECRET: {'✅ Present' if totp_secret else '❌ Missing'}")
print()

if not all([api_key, client_code, password, totp_secret]):
    print("⚠️  CREDENTIALS NOT AVAILABLE")
    print("\n💡 Set environment variables to test:")
    print("   export ANGEL_API_KEY='your_key'")
    print("   export ANGEL_CLIENT_CODE='your_code'")
    print("   export ANGEL_PASSWORD='your_password'")
    print("   export ANGEL_TOTP_SECRET='your_totp_secret'")
    print("\n📌 These are the same credentials used by the trading bot")
    print("   Check: equity/eqcode/config.py")
    sys.exit(1)

# Try to authenticate
print("🔐 Authentication with Angel One:")
print("=" * 70)

try:
    sac = SmartConnect(api_key=api_key)
    print(f"  ✅ SmartConnect initialized")
    
    totp = pyotp.TOTP(totp_secret)
    totp_token = totp.now()
    print(f"  ✅ TOTP generated: {totp_token}")
    
    session = sac.generateSession(client_code, password, totp_token)
    
    if not session or session.get("status") != True:
        print(f"  ❌ Authentication failed")
        print(f"  Error: {session}")
        sys.exit(1)
    
    print(f"  ✅ Authentication successful!")
    print()
    
except Exception as e:
    print(f"  ❌ Error: {e}")
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
        # Prepare request for historical candle data
        params = {
            "mode": "FULL",
            "exchangeTokens": {
                "NSE": [token]
            },
            "interval": "FIVE_MINUTE",
            "fromDate": from_date_str,
            "toDate": to_date_str
        }
        
        # Fetch candles from API
        resp = sac.getCandleData(params)
        
        if resp and resp.get("status"):
            candles_data = resp.get("data", {})
            
            if candles_data and isinstance(candles_data, dict):
                candles = candles_data.get("candles", [])
                
                if candles and isinstance(candles, list) and len(candles) > 0:
                    print(f"✅ {len(candles)} candles")
                    
                    first = candles[0]
                    last = candles[-1]
                    
                    # Format: [timestamp, open, high, low, close, volume]
                    try:
                        results[symbol] = {
                            "count": len(candles),
                            "first": {
                                "ohlc": f"O:{float(first[1]):.2f} H:{float(first[2]):.2f} L:{float(first[3]):.2f} C:{float(first[4]):.2f}",
                                "vol": int(first[5]) if len(first) > 5 else 0
                            },
                            "last": {
                                "ohlc": f"O:{float(last[1]):.2f} H:{float(last[2]):.2f} L:{float(last[3]):.2f} C:{float(last[4]):.2f}",
                                "vol": int(last[5]) if len(last) > 5 else 0
                            }
                        }
                        success_count += 1
                    except (IndexError, ValueError, TypeError) as e:
                        results[symbol] = {"count": 0, "status": "data_parse_error"}
                        print(f"  └─ Data parse error")
                else:
                    print(f"⚠️  No candles returned")
                    results[symbol] = {"count": 0, "status": "no_data"}
            else:
                print(f"❌ Invalid response format")
                results[symbol] = {"count": 0, "status": "invalid_format"}
        else:
            error_msg = resp.get("message", "Unknown error") if resp else "No response"
            print(f"❌ API error: {error_msg[:30]}")
            results[symbol] = {"count": 0, "status": "api_error"}
    
    except Exception as e:
        error_str = str(e)[:50]
        print(f"❌ {error_str}")
        results[symbol] = {"count": 0, "status": "exception", "error": error_str}

print()
print("=" * 70)
print("📈 RESULTS SUMMARY")
print("=" * 70)

for symbol, data in results.items():
    if data.get("count", 0) > 0:
        print(f"\n✅ {symbol}")
        print(f"   Total Candles: {data['count']}")
        print(f"   First OHLC: {data['first']['ohlc']} | Vol: {data['first']['vol']}")
        print(f"   Last  OHLC: {data['last']['ohlc']} | Vol: {data['last']['vol']}")
    else:
        status = data.get("status", "unknown")
        print(f"\n⚠️  {symbol}: {status}")

print()
print("=" * 70)
print(f"📊 SUCCESS RATE: {success_count}/5 symbols")

if success_count >= 3:
    print("\n✅ CANDLE FETCHING IS WORKING CORRECTLY")
    exit_code = 0
elif success_count > 0:
    print("\n⚠️  PARTIAL SUCCESS - Some symbols fetched")
    exit_code = 0
else:
    print("\n❌ CANDLE FETCHING FAILED - No candles retrieved")
    exit_code = 1

print("=" * 70 + "\n")

sys.exit(exit_code)
