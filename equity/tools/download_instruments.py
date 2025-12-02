#!/usr/bin/env python3
"""
Download fresh instruments file from AngelOne
"""

import requests
import json
import os
import sys
from datetime import datetime

def download_instruments():
    """Download instruments file from AngelOne"""
    
    # AngelOne instruments URL
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    
    try:
        print("🔄 Downloading instruments from AngelOne...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Parse JSON to validate
        instruments_data = response.json()
        
        if not isinstance(instruments_data, list) or len(instruments_data) == 0:
            raise ValueError("Invalid instruments data received")
        
        print(f"✅ Downloaded {len(instruments_data)} instruments")
        
        # Create backup of old file
        if os.path.exists("tools/instrument.json"):
            backup_name = f"tools/instrument_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            os.rename("tools/instrument.json", backup_name)
            print(f"📦 Backed up old file to {backup_name}")
        
        # Save new file
        with open("tools/instrument.json", "w") as f:
            json.dump(instruments_data, f, indent=2)
        
        print("✅ Instruments file updated successfully")
        
        # Show some stats
        equity_count = len([i for i in instruments_data if i.get("exch_seg") == "NSE" and "-EQ" in i.get("symbol", "")])
        print(f"📈 NSE Equity instruments: {equity_count}")
        
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error downloading instruments: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in instruments file: {e}")
        return False
    except Exception as e:
        print(f"❌ Error downloading instruments: {e}")
        return False

def validate_instruments():
    """Validate existing instruments file"""
    
    if not os.path.exists("tools/instrument.json"):
        print("❌ No instruments file found")
        return False
    
    try:
        with open("tools/instrument.json", "r") as f:
            data = json.load(f)
        
        if not isinstance(data, list) or len(data) == 0:
            print("❌ Invalid instruments file format")
            return False
        
        # Check if file is recent (within 7 days)
        file_age = datetime.now().timestamp() - os.path.getmtime("tools/instrument.json")
        days_old = file_age / (24 * 3600)
        
        print(f"📅 Instruments file age: {days_old:.1f} days")
        
        if days_old > 7:
            print("⚠️  Instruments file is older than 7 days - consider updating")
            return False
        
        print(f"✅ Instruments file valid ({len(data)} instruments)")
        return True
        
    except Exception as e:
        print(f"❌ Error validating instruments: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        sys.exit(0 if validate_instruments() else 1)
    else:
        sys.exit(0 if download_instruments() else 1)
