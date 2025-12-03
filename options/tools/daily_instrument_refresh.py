#!/usr/bin/env python3
"""
Daily instrument refresh scheduler for options bot.
Downloads fresh instrument.json from AngelOne every morning at market open.
"""

import json
import time
from pathlib import Path
from datetime import datetime, time as dt_time
import schedule
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s'
)
logger = logging.getLogger(__name__)

OUTPUT_FILE = Path(__file__).parent / "instrument.json"

def refresh_instruments():
    """Download and refresh instrument.json from broker"""
    
    logger.info("=" * 70)
    logger.info("SCHEDULED INSTRUMENT REFRESH")
    logger.info("=" * 70)
    
    try:
        from fetch_nfo_instruments import fetch_nfo_instruments
        
        instruments = fetch_nfo_instruments()
        
        if not instruments:
            logger.warning("❌ Failed to fetch instruments")
            return False
        
        # Save to file
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(instruments, f, indent=2)
        
        logger.info(f"✅ Refreshed instrument.json with {len(instruments)} contracts")
        
        # Show summary
        underlyings = set(item.get('name') for item in instruments)
        logger.info(f"   Underlyings: {len(underlyings)} | Contracts: {len(instruments)}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error refreshing instruments: {str(e)}")
        return False

def schedule_daily_refresh():
    """Schedule daily refresh at 9:15 AM (market open)"""
    
    # Schedule at 9:15 AM every day
    schedule.every().day.at("09:15").do(refresh_instruments)
    
    logger.info("✅ Daily instrument refresh scheduled at 09:15 AM")
    
    # Run scheduler in a loop
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

if __name__ == '__main__':
    # Immediate refresh on startup
    logger.info("Starting daily instrument refresh scheduler...")
    refresh_instruments()
    
    # Then schedule for daily refresh
    schedule_daily_refresh()
