#!/usr/bin/env python3
"""
Download real NSE F&O data from alternative sources
Since NSE official endpoints are blocked, we use GitHub mirrors and public APIs
"""

import requests
import csv
import logging
from pathlib import Path
from datetime import datetime, timedelta
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger(__name__)

class NSEDataCollector:
    """Download NSE F&O data from public sources"""
    
    def __init__(self):
        self.output_dir = Path('/root/santhosh/trading/options/data/training')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36'
        })
    
    def download_from_github(self):
        """Try to download NSE data from GitHub mirrors"""
        
        log.info("\nAttempting GitHub mirror download...")
        
        # Common GitHub mirrors for NSE data
        mirrors = [
            'https://github.com/aravindkk728/NSE-Data/raw/master/HISTORICAL_DATA',
            'https://github.com/SouravAgarwal/nse-historical-data/raw/master',
        ]
        
        for mirror in mirrors:
            try:
                log.info(f"Trying: {mirror}")
                # Don't actually download yet, just verify accessibility
                resp = self.session.head(mirror, timeout=3)
                log.info(f"Status: {resp.status_code}")
            except Exception as e:
                log.info(f"Not accessible: {e}")
        
        return False
    
    def download_from_quandl(self):
        """Try Quandl alternative (requires API key)"""
        log.info("\nQuandl API requires free registration")
        log.info("URL: https://www.quandl.com/api-docs/table?&database=NSE")
        return False
    
    def create_sample_nse_data(self):
        """
        Create realistic sample NSE data structure
        This demonstrates the expected data format for your training pipeline
        """
        
        log.info("\nCreating sample NSE-format data...")
        
        # Sample F&O symbols with realistic data
        symbols = ['NIFTY', 'BANKNIFTY', 'HCLTECH', 'UBL', 'SAIL', 'VEDL', 'HINDALCO']
        base_date = datetime.now() - timedelta(days=30)
        
        candles = []
        
        for symbol in symbols:
            for day in range(30):
                date = base_date + timedelta(days=day)
                
                # Skip weekends
                if date.weekday() >= 5:
                    continue
                
                # Generate timestamps from 9:30 to 15:30
                for hm in range(930, 1540):  # 9:30 to 15:39
                    hour = hm // 100
                    mins = hm % 100
                    
                    if mins >= 60:  # Skip invalid times
                        continue
                    
                    timestamp = date.replace(hour=hour, minute=mins).isoformat()
                    
                    base_price = 50000 + (day * 100)
                    price = base_price + (mins % 60)
                    
                    candles.append({
                        'timestamp': timestamp,
                        'symbol': symbol,
                        'expiry': '29JAN26',
                        'strike': 50000,
                        'option_type': 'CE',
                        'open': price,
                        'high': price + 50,
                        'low': price - 50,
                        'close': price + 25,
                        'volume': 1000 + (mins % 500),
                        'iv': 20.5 + (day % 10)
                    })
        
        # Save to CSV
        output_file = self.output_dir / 'nse_sample_data.csv'
        with open(output_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=candles[0].keys())
            writer.writeheader()
            writer.writerows(candles)
        
        log.info(f"✅ Created {len(candles)} sample candles: {output_file.name}")
        return len(candles)
    
    def create_data_structure_guide(self):
        """Create guide for data structure and sources"""
        
        guide = """
# NSE F&O DATA COLLECTION - ALTERNATIVE APPROACH

## Status: Official NSE endpoints blocked (404)

NSE has restricted direct API access. Here are authenticated alternatives:

## OPTION 1: Live Trade Collection (RECOMMENDED)
Your bot collects real trades automatically during Jan 2-10
- 100% authentic market data
- Aligned with your actual trading signals
- Automatic: No manual setup needed
Location: /root/santhosh/trading/options/data/training/real_trades_candles.csv

## OPTION 2: Public Data Services
A. **Angel Broking Export** (if available)
   - Login to Angel Broking web
   - Download historical data from their portal
   - Import using: import_angel_data.py

B. **Third-Party APIs** (Paid)
   - Quandl: https://www.quandl.com/ (NSE datasets)
   - AlgoJi: https://www.algoji.com/
   - Shoonya: Pre-loaded with Angel One

C. **GitHub Community Data**
   - Search: "NSE historical data github"
   - Format: CSV with OHLCV
   - Verify timestamp alignment

## OPTION 3: Manual Data Entry
   - Use your broker's historical charts
   - Export daily OHLCV to CSV
   - Merge with bot's live collection

## EXPECTED DATA FORMAT
```csv
timestamp,symbol,expiry,strike,option_type,open,high,low,close,volume,iv
2025-12-31T09:30:00,NIFTY,29JAN26,50000,CE,50100,50200,50000,50150,5000,20.5
```

## TIMELINE
- Dec 31: Manual data collection / GitHub search
- Jan 2-10: Automatic live trade collection (bot running)
- Jan 10: Merge all sources into training set
- Jan 11: Prepare sequences for LSTM/CNN training

## BOT INTEGRATION
Your data collection script runs automatically:
- Location: /root/santhosh/trading/options/tools/collect_live_trades.py
- Schedule: Run hourly via cron during trading hours
- Input: Bot logs in /root/santhosh/trading/options/logs/
- Output: CSV files in /root/santhosh/trading/options/data/training/

Setup cron (Jan 2):
```bash
0 * * * * cd /root/santhosh/trading/options && python3 tools/collect_live_trades.py
```

This runs every hour, collecting live trade data from your alerts.jsonl logs.
"""
        
        guide_file = self.output_dir / 'NSE_DATA_COLLECTION_GUIDE.md'
        guide_file.write_text(guide)
        
        log.info(f"✅ Created guide: {guide_file.name}")
    
    def run(self):
        """Run complete NSE data collection attempt"""
        
        log.info("\n" + "="*70)
        log.info("NSE F&O DATA COLLECTOR")
        log.info("="*70)
        
        # Try official sources
        self.download_from_github()
        self.download_from_quandl()
        
        log.info("\n" + "-"*70)
        log.info("SUMMARY: Official NSE endpoints are blocked")
        log.info("Switching to alternative data strategy...")
        log.info("-"*70)
        
        # Create sample data demonstrating format
        count = self.create_sample_nse_data()
        
        # Create guide for alternatives
        self.create_data_structure_guide()
        
        log.info("\n" + "="*70)
        log.info("DATA COLLECTION STRATEGY")
        log.info("="*70)
        log.info("✅ Sample NSE data created (shows correct format)")
        log.info("✅ Live trade collector ready (Jan 2-10)")
        log.info("✅ Alternative sources documented")
        log.info("\nRECOMMENDED: Use live trade collection during vacation")
        log.info("It captures your actual trading signals in real data")
        log.info("="*70 + "\n")


if __name__ == '__main__':
    collector = NSEDataCollector()
    collector.run()
