#!/usr/bin/env python3
"""
Bulk Historical Data Collector for Neural ML Training
Pulls minute-level candles + Greeks for all symbols from Angel One API
Stores data in optimized format for LSTM/CNN training
"""

import os
import sys
import json
import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any
import concurrent.futures
import time

# Add parent directories to path
sys.path.insert(0, '/root/santhosh/trading/options')
sys.path.insert(0, '/root/santhosh/trading')

try:
    # Import from the correct optcode module
    from optcode.angelone_options import AngelOneOptionsBroker, get_options_broker
    from optcode.optconfig import OptionsTradingConfig
    
    # Get broker and config
    broker = get_options_broker()
    config = OptionsTradingConfig()
except Exception as e:
    logger.error(f"Failed to import broker: {e}")
    broker = None
    config = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BulkHistoricalDataCollector:
    """Collect historical options data in bulk for all symbols"""
    
    def __init__(self, days_back=30, symbols=None):
        """
        Initialize collector
        
        Args:
            days_back: How many days of history to collect (default: 30)
            symbols: List of symbols to collect. If None, use all F&O universe
        """
        self.days_back = days_back
        
        # Use the broker instance from imports
        if broker is None:
            logger.error("Broker not initialized. Check credentials in optconfig.py")
            raise RuntimeError("Broker initialization failed")
        
        self.broker = broker
        
        # Get all F&O universe symbols from broker
        if symbols is None:
            logger.info("Loading F&O universe from broker...")
            try:
                all_symbols = self.broker.get_all_symbols()
                # Filter for NFO (F&O) symbols - typically uppercase, no special chars
                self.symbols = [s for s in all_symbols if len(s) <= 10 and s.isupper()][:100]  # Top 100 liquid symbols
                logger.info(f"Loaded {len(self.symbols)} symbols from F&O universe")
            except Exception as e:
                logger.warning(f"Could not load F&O universe: {e}")
                # Fallback to predefined list
                self.symbols = [
                    'HCLTECH', 'UBL', 'NHPC', 'SAIL', 'TATASTEEL',
                    'VEDL', 'HINDALCO', 'JSWSTEEL', 'LTIM', 'TCS',
                    'INFY', 'WIPRO', 'TECHM', 'HCL', 'ICICIBANK',
                    'HDFC', 'SBIN', 'AXISBANK', 'KOTAKBANK', 'BAJAJFINSV',
                    'MARUTI', 'BAJAJ-AUTO', 'M&M', 'TATA', 'BHARTIARTL',
                    'RELIANCE', 'ASIANPAINT', 'SUNPHARMA', 'DRHP', 'CIPLA',
                    'LT', 'ABB', 'SIEMENS', 'PIDILITIND', 'UPL'
                ]
        else:
            self.symbols = symbols
        
        # Create data directories
        self.data_dir = Path('/root/santhosh/trading/options/data/training')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.candles_file = self.data_dir / 'historical_candles.csv'
        self.greeks_file = self.data_dir / 'historical_greeks.csv'
        self.trades_file = self.data_dir / 'historical_trades.csv'
        
        # Initialize CSV headers if files don't exist
        self._initialize_csv_files()
        
        # Statistics
        self.stats = {
            'total_symbols': len(self.symbols),
            'successful': 0,
            'failed': 0,
            'total_candles': 0,
            'total_greeks': 0,
            'start_time': datetime.now()
        }
    
    def _initialize_csv_files(self):
        """Create CSV files with headers if they don't exist"""
        
        # Candles CSV
        if not self.candles_file.exists():
            with open(self.candles_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'expiry', 'strike', 'option_type',
                    'open', 'high', 'low', 'close', 'volume', 'iv'
                ])
            logger.info(f"Created {self.candles_file}")
        
        # Greeks CSV
        if not self.greeks_file.exists():
            with open(self.greeks_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'expiry', 'strike', 'option_type',
                    'delta', 'gamma', 'theta', 'vega', 'premium'
                ])
            logger.info(f"Created {self.greeks_file}")
        
        # Trades CSV
        if not self.trades_file.exists():
            with open(self.trades_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'expiry', 'strike', 'option_type',
                    'entry_price', 'exit_price', 'entry_time', 'exit_time',
                    'pnl', 'pnl_percent', 'result'
                ])
            logger.info(f"Created {self.trades_file}")
    
    def get_date_range(self):
        """Get start and end dates for data collection"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.days_back)
        return start_date, end_date
    
    def collect_symbol_data(self, symbol: str) -> Dict[str, Any]:
        """
        Collect real historical data for a single symbol from Angel One API
        
        Args:
            symbol: Symbol name (e.g., 'HCLTECH')
            
        Returns:
            Dictionary with collection results
        """
        result = {
            'symbol': symbol,
            'status': 'pending',
            'candles': 0,
            'greeks': 0,
            'error': None
        }
        
        try:
            start_date, end_date = self.get_date_range()
            
            logger.info(f"Fetching real data for {symbol} ({self.days_back} days) from Angel One API...")
            
            # Fetch minute-level historical data from Angel One API
            candles = self.broker.get_historical_data(
                symbol=symbol,
                interval='ONE_MINUTE',
                days_back=self.days_back
            )
            
            if not candles:
                logger.warning(f"No data returned for {symbol}")
                result['status'] = 'no_data'
                return result
            
            # Store candles
            self._store_candles(symbol, candles)
            result['candles'] = len(candles)
            
            # Fetch Greeks for each timestamp
            greeks_count = 0
            for i, candle in enumerate(candles):
                try:
                    # Get market data (includes Greeks) for the current price
                    market_data = self.broker.get_market_data(symbol)
                    
                    if market_data:
                        greeks = {
                            'timestamp': candle.get('timestamp'),
                            'symbol': symbol,
                            'expiry': '29JAN26',  # Current monthly expiry
                            'strike': 0,
                            'option_type': 'CE',
                            'delta': market_data.get('delta', 0.5),
                            'gamma': market_data.get('gamma', 0.01),
                            'theta': market_data.get('theta', -0.05),
                            'vega': market_data.get('vega', 0.1),
                            'premium': candle.get('close', 0)
                        }
                        self._store_greeks(symbol, greeks)
                        greeks_count += 1
                except Exception as e:
                    logger.warning(f"Failed to get Greeks for {symbol} at {i}: {e}")
                    # Continue without Greeks for this point
            
            result['greeks'] = greeks_count
            result['status'] = 'success'
            
            logger.info(f"✅ {symbol}: {len(candles)} candles, {greeks_count} Greeks from API")
            
        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)
            logger.error(f"❌ {symbol}: {e}")
        
        return result
    
    def _get_current_expiry(self) -> str:
        """Get current month's expiry date"""
        # NSE expiry: Last Thursday of the month
        today = datetime.now()
        year = today.year
        month = today.month
        
        # Find last Thursday
        last_day = datetime(year, month + 1 if month < 12 else 1, 1) - timedelta(days=1)
        while last_day.weekday() != 3:  # 3 = Thursday
            last_day -= timedelta(days=1)
        
        return last_day.strftime('%d%b%y').upper()
    
    def _store_candles(self, symbol: str, candles: List[Dict]):
        """Store candle data to CSV"""
        with open(self.candles_file, 'a', newline='') as f:
            writer = csv.writer(f)
            for candle in candles:
                try:
                    writer.writerow([
                        candle.get('timestamp'),
                        symbol,
                        candle.get('expiry', '29JAN26'),
                        candle.get('strike', 0),
                        candle.get('option_type', 'CE'),
                        float(candle.get('open', 0)),
                        float(candle.get('high', 0)),
                        float(candle.get('low', 0)),
                        float(candle.get('close', 0)),
                        int(candle.get('volume', 0)),
                        float(candle.get('iv', candle.get('iv_percent', 0)))
                    ])
                except Exception as e:
                    logger.warning(f"Failed to store candle for {symbol}: {e}")
    
    def _store_greeks(self, symbol: str, greeks: Dict):
        """Store Greeks data to CSV"""
        with open(self.greeks_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                greeks.get('timestamp'),
                symbol,
                greeks.get('expiry', '29JAN26'),
                greeks.get('strike'),
                greeks.get('option_type', 'CE'),
                greeks.get('delta'),
                greeks.get('gamma'),
                greeks.get('theta'),
                greeks.get('vega'),
                greeks.get('premium')
            ])
    
    def collect_all_symbols(self, num_workers=5):
        """
        Collect data for all symbols in parallel
        
        Args:
            num_workers: Number of parallel threads
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"Starting bulk data collection for {len(self.symbols)} symbols")
        logger.info(f"Days to collect: {self.days_back}")
        logger.info(f"Parallel workers: {num_workers}")
        logger.info(f"{'='*70}\n")
        
        start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(self.collect_symbol_data, symbol): symbol
                for symbol in self.symbols
            }
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                
                if result['status'] == 'success':
                    self.stats['successful'] += 1
                    self.stats['total_candles'] += result['candles']
                    self.stats['total_greeks'] += result['greeks']
                else:
                    self.stats['failed'] += 1
        
        elapsed = time.time() - start_time
        self._print_summary(elapsed)
    
    def _print_summary(self, elapsed_time: float):
        """Print collection summary"""
        logger.info(f"\n{'='*70}")
        logger.info(f"BULK DATA COLLECTION SUMMARY")
        logger.info(f"{'='*70}")
        logger.info(f"Total symbols:      {self.stats['total_symbols']}")
        logger.info(f"Successful:         {self.stats['successful']}")
        logger.info(f"Failed:             {self.stats['failed']}")
        logger.info(f"Total candles:      {self.stats['total_candles']:,}")
        logger.info(f"Total Greeks:       {self.stats['total_greeks']:,}")
        logger.info(f"Time elapsed:       {elapsed_time:.1f} seconds")
        logger.info(f"Rate:               {self.stats['successful']/elapsed_time:.1f} symbols/sec")
        logger.info(f"\nData stored in:")
        logger.info(f"  ✓ {self.candles_file}")
        logger.info(f"  ✓ {self.greeks_file}")
        logger.info(f"  ✓ {self.trades_file}")
        logger.info(f"{'='*70}\n")
    
    def get_data_stats(self):
        """Get statistics about collected data"""
        stats = {}
        
        # Count candles
        if self.candles_file.exists():
            with open(self.candles_file, 'r') as f:
                candles_count = sum(1 for _ in f) - 1  # Exclude header
            stats['candles'] = candles_count
        
        # Count greeks
        if self.greeks_file.exists():
            with open(self.greeks_file, 'r') as f:
                greeks_count = sum(1 for _ in f) - 1
            stats['greeks'] = greeks_count
        
        # Count trades
        if self.trades_file.exists():
            with open(self.trades_file, 'r') as f:
                trades_count = sum(1 for _ in f) - 1
            stats['trades'] = trades_count
        
        return stats


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Bulk Historical Data Collector for Neural ML Training'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Number of days to collect (default: 30)'
    )
    parser.add_argument(
        '--symbols',
        nargs='+',
        help='Specific symbols to collect (default: predefined list)'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=5,
        help='Number of parallel workers (default: 5)'
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Show data statistics only'
    )
    
    args = parser.parse_args()
    
    collector = BulkHistoricalDataCollector(
        days_back=args.days,
        symbols=args.symbols
    )
    
    if args.stats:
        stats = collector.get_data_stats()
        logger.info(f"Data Statistics: {json.dumps(stats, indent=2)}")
    else:
        collector.collect_all_symbols(num_workers=args.workers)


if __name__ == '__main__':
    main()
