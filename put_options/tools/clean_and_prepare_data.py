#!/usr/bin/env python3
"""
Clean training data and prepare for neural ML training
Remove NaN values from Greeks calculations
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def clean_training_data():
    """Clean the generated training data"""
    
    data_file = Path('/root/santhosh/trading/put_options/data/training/nse_fo_training_data.csv')
    output_file = Path('/root/santhosh/trading/put_options/data/training/nse_fo_training_data_clean.csv')
    
    logger.info("="*70)
    logger.info("CLEANING F&O TRAINING DATA")
    logger.info("="*70)
    
    # Load data
    logger.info(f"\n📥 Loading training data from: {data_file}")
    df = pd.read_csv(data_file)
    
    initial_count = len(df)
    logger.info(f"   Initial records: {initial_count:,}")
    logger.info(f"   NaN values: {df.isnull().sum().sum():,}")
    
    # Show NaN distribution
    nan_by_column = df.isnull().sum()
    if nan_by_column.sum() > 0:
        logger.info("\n   NaN distribution by column:")
        for col, count in nan_by_column[nan_by_column > 0].items():
            pct = (count / len(df)) * 100
            logger.info(f"     • {col}: {count:,} ({pct:.2f}%)")
    
    # Remove rows with any NaN values (mainly from Greeks calculations)
    logger.info(f"\n🧹 Removing rows with NaN values...")
    df_clean = df.dropna()
    
    removed = initial_count - len(df_clean)
    logger.info(f"   Removed: {removed:,} rows ({(removed/initial_count)*100:.2f}%)")
    logger.info(f"   Remaining: {len(df_clean):,} clean records")
    
    # Verify data quality
    logger.info(f"\n✅ Data Quality Check:")
    logger.info(f"   NaN values: {df_clean.isnull().sum().sum()}")
    logger.info(f"   Duplicates: {df_clean.duplicated().sum()}")
    logger.info(f"   Unique symbols: {df_clean['symbol'].nunique()}")
    
    # Save clean data
    logger.info(f"\n💾 Saving clean data to: {output_file}")
    df_clean.to_csv(output_file, index=False)
    
    file_size = output_file.stat().st_size / (1024*1024)
    logger.info(f"   File size: {file_size:.2f} MB")
    
    # Statistics
    logger.info(f"\n📊 Clean Dataset Statistics:")
    logger.info(f"   Total Records: {len(df_clean):,}")
    logger.info(f"   Symbols: {df_clean['symbol'].nunique()}")
    logger.info(f"   Strikes: {df_clean['strike'].nunique()}")
    logger.info(f"   Columns: {len(df_clean.columns)}")
    
    # Symbol breakdown
    logger.info(f"\n🔢 Records per symbol (sample):")
    symbol_counts = df_clean['symbol'].value_counts().sort_index()
    for symbol in list(symbol_counts.index)[:10]:
        count = symbol_counts[symbol]
        logger.info(f"   • {symbol}: {count}")
    logger.info(f"   ... and {len(symbol_counts) - 10} more symbols")
    
    logger.info("\n" + "="*70)
    logger.info("✅ DATA CLEANING COMPLETE")
    logger.info("="*70)
    logger.info(f"Ready for neural ML training: {output_file}")
    
    return output_file

if __name__ == '__main__':
    clean_training_data()
