#!/usr/bin/env python3
"""
Download NSE NFO (Options) Instruments from Angel One API

This fetches all options contracts for:
- BANKNIFTY
- NIFTY
- FINNIFTY

And saves to instrument.json for token mapping
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from optcode.angelone import AngelOneAPI
from optcode.optconfig import OptionsTradingConfig, logger


def download_options_instruments():
    """Download NFO instruments from Angel One"""
    
    logger.info("Starting NFO instruments download...")
    
    try:
        # Initialize API
        api = AngelOneAPI()
        
        # Authenticate
        auth_result = api.authenticate()
        if not auth_result:
            logger.error("❌ Failed to authenticate with Angel One")
            return False
        
        logger.info("✅ Authenticated with Angel One")
        
        # Get instruments
        logger.info("Fetching NFO instruments...")
        instruments = api.get_instruments()
        
        if not instruments:
            logger.error("❌ No instruments returned from API")
            return False
        
        logger.info(f"✅ Fetched {len(instruments)} total instruments")
        
        # Filter for NFO (options) only
        nfo_instruments = [
            inst for inst in instruments 
            if inst.get('exch_seg') == 'NFO'
        ]
        
        logger.info(f"✅ Found {len(nfo_instruments)} NFO contracts")
        
        # Further filter for our underlyings
        underlyings = ['BANKNIFTY', 'NIFTY', 'FINNIFTY']
        options_contracts = [
            inst for inst in nfo_instruments
            if any(underlying in inst.get('symbol', '') for underlying in underlyings)
        ]
        
        logger.info(f"✅ Found {len(options_contracts)} option contracts for {underlyings}")
        
        # Save to file
        output_path = Path(__file__).parent / "instrument.json"
        
        with open(output_path, 'w') as f:
            json.dump(options_contracts, f, indent=2)
        
        logger.info(f"✅ Saved {len(options_contracts)} contracts to {output_path}")
        
        # Print stats
        print("\n" + "="*70)
        print("NFO INSTRUMENTS DOWNLOAD SUMMARY")
        print("="*70)
        print(f"Total instruments fetched: {len(instruments)}")
        print(f"NFO contracts found: {len(nfo_instruments)}")
        print(f"Options contracts (BANKNIFTY/NIFTY/FINNIFTY): {len(options_contracts)}")
        print(f"Saved to: {output_path}")
        print(f"Downloaded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70 + "\n")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error downloading instruments: {str(e)}")
        return False


if __name__ == "__main__":
    success = download_options_instruments()
    sys.exit(0 if success else 1)
