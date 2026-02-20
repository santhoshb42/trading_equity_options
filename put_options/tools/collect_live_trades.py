#!/usr/bin/env python3
"""
Collect real trade data from bot logs for neural ML training
Runs automatically alongside your trading bot
Converts your live trades into training format
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LiveTradeCollector:
    """Collect real trade data from bot logs"""
    
    def __init__(self):
        self.logs_dir = Path('/root/santhosh/trading/options/logs')
        self.training_dir = Path('/root/santhosh/trading/put_options/data/training')
        self.training_dir.mkdir(parents=True, exist_ok=True)
        
    def collect_from_alerts_log(self) -> Dict[str, Any]:
        """Extract trade data from alerts.jsonl logs"""
        
        logger.info("Scanning for alert logs...")
        
        trades_collected = 0
        candles_data = []
        greeks_data = []
        
        # Scan all daily log directories
        for log_date_dir in sorted(self.logs_dir.glob('*')):
            if not log_date_dir.is_dir():
                continue
            
            alerts_file = log_date_dir / 'alerts.jsonl'
            if not alerts_file.exists():
                continue
            
            logger.info(f"Processing {alerts_file}")
            
            try:
                with open(alerts_file, 'r') as f:
                    for line in f:
                        try:
                            alert = json.loads(line)
                            
                            # Extract candle-like data from alert
                            if 'alert' in alert and alert['alert'].get('symbol'):
                                symbol = alert['alert']['symbol']
                                timestamp = alert.get('timestamp', datetime.now().isoformat())
                                
                                # Create candle entry
                                candle = {
                                    'timestamp': timestamp,
                                    'symbol': symbol,
                                    'expiry': '29JAN26',  # Current month
                                    'strike': 0,
                                    'option_type': 'CE',
                                    'open': alert['alert'].get('price', 0),
                                    'high': alert['alert'].get('price', 0),
                                    'low': alert['alert'].get('price', 0),
                                    'close': alert['alert'].get('price', 0),
                                    'volume': 0,
                                    'iv': 0
                                }
                                candles_data.append(candle)
                                trades_collected += 1
                                
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"Error reading {alerts_file}: {e}")
        
        return {
            'trades_collected': trades_collected,
            'candles': candles_data,
            'greeks': greeks_data
        }
    
    def save_collected_data(self, data: Dict[str, Any]):
        """Save collected data to training format"""
        
        if not data['candles']:
            logger.warning("No trade data collected")
            return
        
        # Save candles
        candles_df = pd.DataFrame(data['candles'])
        candles_file = self.training_dir / 'real_trades_candles.csv'
        candles_df.to_csv(candles_file, index=False)
        logger.info(f"✅ Saved {len(candles_df)} candles to {candles_file.name}")
        
        # Save Greeks if available
        if data['greeks']:
            greeks_df = pd.DataFrame(data['greeks'])
            greeks_file = self.training_dir / 'real_trades_greeks.csv'
            greeks_df.to_csv(greeks_file, index=False)
            logger.info(f"✅ Saved {len(greeks_df)} Greeks entries to {greeks_file.name}")
    
    def create_training_dataset(self):
        """Create combined training dataset from all sources"""
        
        logger.info("\nCreating training dataset...")
        
        # Load existing data
        try:
            candles = pd.read_csv(self.training_dir / 'historical_candles.csv')
            logger.info(f"Loaded {len(candles)} existing candles")
        except FileNotFoundError:
            candles = pd.DataFrame()
        
        try:
            real_trades = pd.read_csv(self.training_dir / 'real_trades_candles.csv')
            logger.info(f"Loaded {len(real_trades)} real trade candles")
            candles = pd.concat([candles, real_trades], ignore_index=True)
        except FileNotFoundError:
            pass
        
        if len(candles) == 0:
            logger.warning("No data available yet")
            return
        
        # Remove duplicates
        candles = candles.drop_duplicates(subset=['timestamp', 'symbol'])
        logger.info(f"Combined dataset: {len(candles)} unique candles")
        
        # Save combined dataset
        combined_file = self.training_dir / 'training_data_combined.csv'
        candles.to_csv(combined_file, index=False)
        logger.info(f"✅ Saved combined training data to {combined_file.name}")
        
        # Statistics
        print("\n" + "="*70)
        print("TRAINING DATA STATISTICS")
        print("="*70)
        print(f"Total data points: {len(candles)}")
        print(f"Symbols: {candles['symbol'].nunique()}")
        print(f"Date range: {candles['timestamp'].min()} to {candles['timestamp'].max()}")
        print(f"Data size: {combined_file.stat().st_size / 1024 / 1024:.2f} MB")
        print()
        
    def run(self):
        """Run full collection and preparation"""
        
        logger.info("\n" + "="*70)
        logger.info("LIVE TRADE DATA COLLECTOR")
        logger.info("="*70 + "\n")
        
        # Collect data
        data = self.collect_from_alerts_log()
        
        if data['trades_collected'] == 0:
            logger.info("\n⚠️  No trades found yet")
            logger.info("This collector will populate as you trade starting Jan 2")
            return
        
        # Save data
        self.save_collected_data(data)
        
        # Create training dataset
        self.create_training_dataset()
        
        logger.info("\n✅ Data collection complete!")


def main():
    collector = LiveTradeCollector()
    collector.run()


if __name__ == '__main__':
    main()
