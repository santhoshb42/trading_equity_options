#!/usr/bin/env python3
"""
Fetch authentic historical F&O data from NSE for the past 30 trading days
Real market data - no synthetic generation
"""

import requests
import pandas as pd
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class NSEHistoricalDataFetcher:
    """Fetch real historical data from NSE"""
    
    def __init__(self):
        self.data_dir = Path('/root/santhosh/trading/put_options/data/training')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # NSE endpoints
        self.nse_base = "https://www.nseindia.com"
        self.fo_history_url = f"{self.nse_base}/products/content/derivatives/equities/fo_history.csv"
        
        # Session with proper headers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def fetch_fo_history(self) -> pd.DataFrame:
        """Fetch F&O historical data from NSE"""
        try:
            logger.info("Fetching F&O historical data from NSE...")
            logger.info(f"URL: {self.fo_history_url}")
            
            response = self.session.get(self.fo_history_url, timeout=30)
            response.raise_for_status()
            
            # Parse CSV
            data = pd.read_csv(pd.io.common.StringIO(response.text))
            
            logger.info(f"✅ Successfully fetched {len(data)} records from NSE")
            logger.info(f"Columns: {list(data.columns)}")
            
            return data
            
        except Exception as e:
            logger.error(f"❌ Failed to fetch from NSE: {e}")
            logger.info("Trying alternative approach - downloading from NSE website directly...")
            return self._fetch_from_alternative_source()
    
    def _fetch_from_alternative_source(self) -> pd.DataFrame:
        """Try alternative NSE data sources"""
        try:
            # NSE Equity Derivatives F&O data
            url = "https://www1.nseindia.com/LatestRpts/fo_seclist.csv"
            
            logger.info(f"Trying alternative URL: {url}")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = pd.read_csv(pd.io.common.StringIO(response.text))
            logger.info(f"✅ Got {len(data)} records from alternative source")
            
            return data
            
        except Exception as e:
            logger.error(f"❌ Alternative source failed: {e}")
            logger.warning("NSE website may require additional authentication")
            return None
    
    def fetch_bhavcopy_data(self, days_back=30) -> pd.DataFrame:
        """Fetch Bhavcopy data (daily settlement prices) for past 30 days"""
        try:
            logger.info(f"\nFetching Bhavcopy data for last {days_back} days...")
            
            all_data = []
            current_date = datetime.now()
            
            # Go back 30 trading days (approximately 42 calendar days)
            start_date = current_date - timedelta(days=days_back * 1.5)
            
            for i in range(int(days_back * 1.5)):
                date = start_date + timedelta(days=i)
                
                # Skip weekends
                if date.weekday() >= 5:
                    continue
                
                # NSE Bhavcopy format
                date_str = date.strftime('%d%b%Y')
                url = f"https://www1.nseindia.com/archives/fo/bhav/fo{date_str}bhav.csv"
                
                try:
                    logger.info(f"Fetching {date_str}...")
                    response = self.session.get(url, timeout=10)
                    
                    if response.status_code == 200:
                        data = pd.read_csv(pd.io.common.StringIO(response.text))
                        all_data.append(data)
                        logger.info(f"  ✓ Got {len(data)} records")
                        time.sleep(1)  # Be gentle to NSE servers
                    else:
                        logger.warning(f"  ✗ No data for {date_str}")
                        
                except Exception as e:
                    logger.warning(f"  ✗ Error fetching {date_str}: {e}")
                    continue
            
            if all_data:
                combined = pd.concat(all_data, ignore_index=True)
                logger.info(f"\n✅ Total records fetched: {len(combined)}")
                logger.info(f"Columns: {list(combined.columns)}")
                return combined
            else:
                logger.error("❌ No data fetched from Bhavcopy")
                return None
                
        except Exception as e:
            logger.error(f"❌ Bhavcopy fetch failed: {e}")
            return None
    
    def process_and_save_data(self, data: pd.DataFrame) -> str:
        """Process and save F&O data"""
        if data is None or len(data) == 0:
            logger.error("No data to process")
            return None
        
        try:
            logger.info("\nProcessing data...")
            
            # Filter for F&O data
            if 'INSTRUMENT' in data.columns:
                # Bhavcopy format
                fo_data = data[data['INSTRUMENT'].isin(['FUTSTK', 'FUTIDX', 'OPTSTK', 'OPTIDX'])].copy()
            elif 'Instrument' in data.columns:
                fo_data = data[data['Instrument'].isin(['FUTSTK', 'FUTIDX', 'OPTSTK', 'OPTIDX'])].copy()
            else:
                # No instrument column, save as-is
                fo_data = data.copy()
            
            logger.info(f"Filtered F&O records: {len(fo_data)}")
            
            # Save to CSV
            output_file = self.data_dir / 'nse_fo_historical_data.csv'
            fo_data.to_csv(output_file, index=False)
            
            logger.info(f"✅ Saved to {output_file}")
            logger.info(f"   Size: {output_file.stat().st_size / (1024*1024):.2f} MB")
            logger.info(f"   Rows: {len(fo_data)}")
            
            # Generate summary
            self._generate_summary(fo_data)
            
            return str(output_file)
            
        except Exception as e:
            logger.error(f"❌ Processing failed: {e}")
            return None
    
    def _generate_summary(self, data: pd.DataFrame):
        """Generate data summary"""
        logger.info("\n" + "="*70)
        logger.info("DATA SUMMARY")
        logger.info("="*70)
        
        # Show column info
        logger.info(f"\nColumns ({len(data.columns)}):")
        for col in data.columns:
            logger.info(f"  • {col}")
        
        # Show instruments
        if 'INSTRUMENT' in data.columns:
            logger.info(f"\nInstruments:")
            for inst in data['INSTRUMENT'].unique():
                count = len(data[data['INSTRUMENT'] == inst])
                logger.info(f"  • {inst}: {count} records")
        
        # Show symbols
        if 'SYMBOL' in data.columns:
            logger.info(f"\nTop 20 Symbols:")
            for sym in data['SYMBOL'].unique()[:20]:
                count = len(data[data['SYMBOL'] == sym])
                logger.info(f"  • {sym}: {count} records")
    
    def run(self):
        """Execute full data fetch"""
        logger.info("="*70)
        logger.info("NSE HISTORICAL F&O DATA FETCHER")
        logger.info("="*70 + "\n")
        
        # Try multiple approaches
        logger.info("APPROACH 1: Fetching F&O list...")
        data = self.fetch_fo_history()
        
        if data is None or len(data) < 100:
            logger.info("\nAPPROACH 2: Fetching Bhavcopy (daily settlement data)...")
            data = self.fetch_bhavcopy_data(days_back=30)
        
        if data is not None and len(data) > 0:
            output_file = self.process_and_save_data(data)
            
            if output_file:
                logger.info(f"\n✅ COMPLETE! Authentic NSE data ready at:")
                logger.info(f"   {output_file}")
                logger.info(f"\n🎯 Next: Train neural models on this real data")
                return True
        
        logger.error("\n❌ FAILED: Could not fetch NSE data")
        logger.info("Alternative: Use your trading logs starting Jan 2")
        return False


def main():
    fetcher = NSEHistoricalDataFetcher()
    success = fetcher.run()
    return 0 if success else 1


if __name__ == '__main__':
    exit(main())
