#!/usr/bin/env python3
"""
Fetch NFO instruments from AngelOne SmartAPI
Downloads real option contracts daily from broker.
Falls back to synthetic generation if API unavailable.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

try:
    from SmartApi import SmartConnect
except ImportError:
    SmartConnect = None

try:
    import pyotp
except ImportError:
    pyotp = None

# Add parent dir to path to import config
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from optcode.optconfig import AngelOneConfig
except ImportError:
    print("Warning: Could not import AngelOneConfig")
    AngelOneConfig = None

OUTPUT_FILE = Path(__file__).parent / "instrument.json"

def fetch_from_angelone():
    """Fetch real NFO instruments from AngelOne SmartAPI
    
    Note: SmartConnect doesn't have a bulk instrument download method.
    We'll use searchScrip for specific symbols instead.
    For complete instrument data, we recommend downloading from NSE/BSE website
    or using the broker's instrument master file.
    """
    
    if not SmartConnect or not AngelOneConfig:
        print("⚠️ SmartConnect or config not available")
        return None
    
    if not AngelOneConfig.TOTP_KEY:
        print("⚠️ TOTP_KEY not configured")
        return None
    
    try:
        print("🔄 Connecting to AngelOne SmartAPI...")
        
        # Generate TOTP code from secret key
        if pyotp:
            totp = pyotp.TOTP(AngelOneConfig.TOTP_KEY)
            totp_code = totp.now()
            print(f"📱 Generated TOTP: {totp_code}")
        else:
            totp_code = AngelOneConfig.TOTP_KEY
            print("⚠️ pyotp not installed, using TOTP_KEY directly")
        
        # Create SmartConnect instance
        obj = SmartConnect(api_key=AngelOneConfig.API_KEY)
        
        # Login
        print("🔐 Authenticating...")
        data = obj.generateSession(
            AngelOneConfig.CLIENT_CODE,
            AngelOneConfig.PASSWORD,
            totp_code
        )
        
        if data['status'] == False:
            print(f"❌ Authentication failed: {data}")
            return None
        
        print("✅ Authenticated successfully")
        
        # SmartConnect doesn't have bulk instrument download
        # We need to use searchScrip for individual symbols or get from broker's instrument master
        print("📥 Fetching NFO instruments from broker...")
        
        # Complete NSE F&O Universe - ALL approved underlyings
        underlyings = [
            # Indices (3)
            'BANKNIFTY', 'NIFTY', 'FINNIFTY',
            # Banks & Financials (15)
            'AUBANK', 'AXISBANK', 'BANKBARODA', 'CITI', 'HDFCBANK', 'ICICIBANK', 'INDUSINDBK', 'IDFCBANK', 'KOTAK', 'RBLBANK', 'SBICARD', 'SBILIFE', 'SBIN', 'YESBANK', 'FEDERALBNK',
            # IT (8)
            'INFY', 'TCS', 'TECHM', 'WIPRO', 'HCL', 'LTIM', 'KPITTECH', 'MFSL',
            # Energy & Utilities (8)
            'RELIANCE', 'TATASTEEL', 'TATAPOWER', 'NTPC', 'GAIL', 'BPCL', 'COALINDIA', 'POWERGRID',
            # Consumption (10)
            'ASIANPAINT', 'BRITANNIA', 'COLPAL', 'DABUR', 'HINDUSTAN', 'INDIGO', 'ITC', 'MARUTI', 'NESTLEIND', 'UNILEVER',
            # Pharma (8)
            'APOLLOHOSP', 'AUROPHARMA', 'BIOCON', 'CIPLA', 'DRREDDY', 'GLDRX', 'LUPIN', 'SUNPHARMA',
            # Infra & Realty (10)
            'ADANIGREEN', 'ADANIPORTS', 'ADANITRANS', 'ADANIPOWER', 'BHARTIARTL', 'GMRINFRA', 'JSWSTEEL', 'LTTS', 'OBEROIRLTY', 'SUNTV',
            # Chemicals & Materials (6)
            'AMBUJACEM', 'BOSCHLTD', 'HCLTECH', 'HINDALCO', 'HINDCOPPER', 'NMDC',
            # Auto (6)
            'BAJAJ', 'BAJAJFINSV', 'BAJAJHLDNG', 'EICHERMOT', 'HEROMOTOCO', 'TVS',
            # Industrial (8)
            'ABB', 'CUMMINSIND', 'KPILTECH', 'LAXMIMACH', 'NATIONALUM', 'PAGEIND', 'PIIND', 'SIEMENS',
            # Services (4)
            'IRFC', 'IRCTC', 'MOENERGY', 'NAUKRI',
            # Diversified (8)
            'BRENT', 'COTTON', 'CRUDEOIL', 'GOLDGULD', 'SILVER', 'JSWENERGY', 'MRF', 'SYNGENE',
            # Additional Core (8)
            'AMBER', 'BSOFT', 'CAMS', 'GRANULES', 'INDIGO', 'KALYANKJIL', 'MPHASIS', 'OFSS',
            # Additional Approved F&O (12)
            'CENTRALBK', 'CRISIL', 'DEEPAKINDS', 'EXIDEIND', 'FSL', 'GLENMARK', 'GRASIM', 'INOXWIND', 'KPILTECH', 'LICHSGFIN', 'MCLRENSP', 'MOTILALOSWL',
            # Options Market Makers (5)
            'NATCOPHARM', 'NUVOCO', 'PETRONET', 'RBLBANK', 'SYNGENE'
        ]
        
        instruments = []
        
        import time
        
        # For each underlying, search for its option contracts
        for idx, underlying in enumerate(underlyings):
            try:
                # Add rate limiting: wait a bit between requests
                if idx > 0:
                    time.sleep(1)
                
                # searchScrip in NFO exchange for the underlying
                result = obj.searchScrip(exchange='NFO', searchscrip=underlying)
                
                # Handle different response formats
                if result:
                    # Try to extract instruments from result
                    found_instruments = None
                    
                    # Format 1: result.get('data', {}).get('values')
                    if isinstance(result, dict):
                        data = result.get('data', {})
                        if isinstance(data, dict):
                            found_instruments = data.get('values', [])
                        elif isinstance(data, list):
                            found_instruments = data
                    
                    if found_instruments and isinstance(found_instruments, list):
                        print(f"✅ {underlying}: {len(found_instruments)} contracts")
                        instruments.extend(found_instruments)
                    else:
                        print(f"⚠️ {underlying}: Could not parse response")
                else:
                    print(f"⚠️ {underlying}: Empty response")
                    
            except Exception as e:
                error_str = str(e)
                if 'exceeding access rate' in error_str or 'rate' in error_str.lower():
                    print(f"⚠️ {underlying}: Rate limited (continuing anyway...)")
                    time.sleep(2)  # Back off on rate limit
                else:
                    print(f"⚠️ {underlying}: {error_str[:80]}")
                continue
        
        if instruments:
            print(f"\n✅ Fetched {len(instruments)} NFO instruments total from broker")
            return instruments
        else:
            print("\n❌ No instruments retrieved from broker")
            return None
        
    except Exception as e:
        print(f"❌ Error fetching from AngelOne: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def fetch_nfo_instruments():
    """Main function: Try to fetch from broker, fallback to synthetic"""
    
    # Try to fetch from AngelOne first
    print("=" * 70)
    print("  INSTRUMENT DOWNLOAD - AngelOne NFO")
    print("=" * 70)
    
    broker_instruments = fetch_from_angelone()
    
    if broker_instruments:
        print(f"\n✅ Successfully fetched {len(broker_instruments)} real instruments from broker")
        # Convert broker format to our standard format
        instruments = convert_broker_format_to_standard(broker_instruments)
        return instruments
    else:
        print("\n⚠️ Broker fetch failed, generating synthetic instruments...")
        return generate_synthetic_nfo()

def convert_broker_format_to_standard(broker_instruments):
    """Convert AngelOne broker response format to our standard instrument.json format
    
    AngelOne symbol format: {SYMBOL}{DD}{MMM}{YY}{STRIKE}{CE/PE}
    Example: AMBER30DEC2510000CE
    - AMBER = underlying symbol
    - 30 = day of month
    - DEC = month
    - 25 = year (2025, so 26 = 2026)
    - 10000 = strike price in paise (100 paise = ₹1, so 10000 paise = ₹100)
    - CE = call option (PE for put)
    """
    
    import re
    from datetime import datetime
    
    instruments = []
    
    for instr in broker_instruments:
        if not isinstance(instr, dict):
            continue
        
        symbol = instr.get('tradingsymbol', instr.get('symbol', ''))
        
        # Only include NFO instruments that end with CE or PE
        if not symbol or not symbol.endswith(('CE', 'PE')):
            continue
        
        # Parse format: {SYMBOL}{DD}{MMM}{YY}{STRIKE}{CE/PE}
        # Example: AMBER30DEC2510000CE
        match = re.match(r'^([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$', symbol)
        
        if not match:
            continue
        
        underlying, day, month, year_str, strike_paise_str, option_type = match.groups()
        
        # Convert year (25 = 2025, 26 = 2026, etc.)
        try:
            year_int = int(year_str)
            full_year = 2000 + year_int if year_int < 100 else year_int
        except:
            continue
        
        # Parse expiry date
        try:
            expiry_date = datetime.strptime(f'{full_year}{day}{month}', '%Y%d%b')
            expiry = expiry_date.strftime('%Y-%m-%d')
        except:
            continue
        
        # Convert strike from paise to rupees
        # 10000 paise = ₹100, 67000 paise = ₹670, etc.
        try:
            strike_paise = int(strike_paise_str)
            strike_rupees = strike_paise / 100
            # If it's a whole number, use int; otherwise use float
            strike = int(strike_rupees) if strike_rupees == int(strike_rupees) else strike_rupees
        except:
            continue
        
        # Build standard instrument record
        instrument = {
            'token': str(instr.get('symboltoken', '')),
            'symbol': symbol,  # Raw symbol from broker (e.g., AMBER30DEC2510000CE)
            'name': underlying,  # Extracted underlying name
            'expiry': expiry,  # Parsed expiry date (e.g., 2025-12-30)
            'strike': str(int(strike)) if isinstance(strike, float) and strike == int(strike) else str(strike),
            'lotsize': '1',
            'instrumenttype': 'OPTSTK',
            'exch_seg': 'NFO',
            'tick_size': '0.05'
        }
        
        instruments.append(instrument)
    
    return instruments

def generate_synthetic_nfo():
    """Generate synthetic NFO instruments for testing"""
    
    instruments = []
    token_counter = 100000
    
    # Common F&O underlyings with their strike intervals based on price
    # Price rule: <500: 5-10, 500-2000: 10-20, >2000: 50
    underlyings = {
        # Indices
        'BANKNIFTY': {'interval': 100, 'spot': 47000},
        'NIFTY': {'interval': 50, 'spot': 23500},
        'FINNIFTY': {'interval': 100, 'spot': 22000},
        
        # F&O Stocks from today's alerts with real spot prices and intervals
        'AMBER': {'interval': 50, 'spot': 7066},           # High price, 50-point
        'BSOFT': {'interval': 10, 'spot': 417.6},          # <500, 10-point
        'GRANULES': {'interval': 10, 'spot': 573},         # <500, 10-point
        'INDUSINDBK': {'interval': 10, 'spot': 855.35},    # <2000, 10-point
        'MPHASIS': {'interval': 20, 'spot': 2862},         # 1000-2000, 20-point
        'OFSS': {'interval': 50, 'spot': 8131.5},          # High price, 50-point
        
        # Previously supported stocks
        'POWERINDIA': {'interval': 50, 'spot': 22755, 'has_strikes': [22500, 22550, 22600, 22650, 22700, 22750, 22850, 22900, 22950, 23000, 23050, 23100, 23150, 23200]},
        'TCS': {'interval': 50, 'spot': 4125},
        'INFY': {'interval': 50, 'spot': 2890},
        'RELIANCE': {'interval': 10, 'spot': 1285},
        'TECHM': {'interval': 10, 'spot': 1677},
        'ASIANPAINT': {'interval': 20, 'spot': 2955},
    }
    
    # Expiries (next 3 weekly Thursdays + monthly)
    expiries = ['2025-12-04', '2025-12-11', '2025-12-18', '2025-12-25']
    
    for underlying, config in underlyings.items():
        spot = config.get('spot', 1000)
        interval = config.get('interval', 50)
        custom_strikes = config.get('has_strikes', None)
        
        for expiry in expiries:
            # Generate strikes
            if custom_strikes:
                strikes = custom_strikes
            else:
                # Generate strikes around spot (15 strikes centered on ATM)
                atm = int((spot // interval) * interval)
                strikes = [atm + (i * interval) for i in range(-7, 8)]  # 15 strikes
            
            for strike in strikes:
                # Convert expiry to format like "25DEC" (YY + MON)
                expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
                expiry_str = expiry_date.strftime('%y%b').upper()  # e.g., "25DEC"
                
                # CE contract
                ce_symbol = f"{underlying}{expiry_str}{int(strike)}CE"
                instruments.append({
                    'token': str(token_counter),
                    'symbol': ce_symbol,
                    'name': underlying,
                    'expiry': expiry,
                    'strike': str(strike),
                    'lotsize': '1',
                    'instrumenttype': 'OPTSTK',
                    'exch_seg': 'NFO',
                    'tick_size': '0.05'
                })
                token_counter += 1
                
                # PE contract
                pe_symbol = f"{underlying}{expiry_str}{int(strike)}PE"
                instruments.append({
                    'token': str(token_counter),
                    'symbol': pe_symbol,
                    'name': underlying,
                    'expiry': expiry,
                    'strike': str(strike),
                    'lotsize': '1',
                    'instrumenttype': 'OPTSTK',
                    'exch_seg': 'NFO',
                    'tick_size': '0.05'
                })
                token_counter += 1
    
    return instruments

if __name__ == '__main__':
    instruments = fetch_nfo_instruments()
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(instruments, f, indent=2)
    
    print(f"✅ Saved {len(instruments)} NFO instruments to {OUTPUT_FILE}")
    
    # Show sample
    from collections import defaultdict
    by_underlying = defaultdict(list)
    for instr in instruments:
        by_underlying[instr['name']].append(instr)
    
    print(f"\nInstruments by underlying:")
    for underlying in sorted(by_underlying.keys()):
        count = len(by_underlying[underlying])
        print(f"  {underlying}: {count} contracts")
    
    # Check POWERINDIA specifically
    powerindia = [i for i in instruments if i['name'] == 'POWERINDIA' and '2025-12-04' in i.get('expiry', '')]
    powerindia_strikes = sorted(set(int(i['strike']) for i in powerindia if i['instrumenttype'] == 'OPTSTK'))
    print(f"\nPOWERINDIA 2025-12-04 strikes: {powerindia_strikes}")
    print(f"23000 available? {23000 in powerindia_strikes}")
    print(f"22800 available? {22800 in powerindia_strikes}")
