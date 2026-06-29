#!/usr/bin/env python3
"""
Data Preparation Pipeline for Neural ML Training
Converts collected historical data into training-ready format
Includes feature engineering, normalization, sequence creation
"""

import os
import sys
import json
import csv
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Any
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DataPreparationPipeline:
    """Prepare historical data for LSTM/CNN neural training"""
    
    def __init__(self):
        """Initialize data pipeline"""
        self.data_dir = Path('/root/santhosh/trading/options/data/training')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.candles_file = self.data_dir / 'historical_candles.csv'
        self.greeks_file = self.data_dir / 'historical_greeks.csv'
        self.trades_file = self.data_dir / 'historical_trades.csv'
        
        self.output_dir = self.data_dir / 'prepared'
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def load_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load historical data from CSV files"""
        logger.info("Loading historical data...")
        
        candles_df = pd.read_csv(self.candles_file) if self.candles_file.exists() else pd.DataFrame()
        greeks_df = pd.read_csv(self.greeks_file) if self.greeks_file.exists() else pd.DataFrame()
        trades_df = pd.read_csv(self.trades_file) if self.trades_file.exists() else pd.DataFrame()
        
        logger.info(f"  Candles: {len(candles_df)} rows")
        logger.info(f"  Greeks: {len(greeks_df)} rows")
        logger.info(f"  Trades: {len(trades_df)} rows")
        
        return candles_df, greeks_df, trades_df
    
    def merge_data(self, candles_df: pd.DataFrame, greeks_df: pd.DataFrame) -> pd.DataFrame:
        """Merge candles with Greeks on timestamp and symbol"""
        logger.info("Merging candles with Greeks...")
        
        # Convert timestamps to datetime
        candles_df['timestamp'] = pd.to_datetime(candles_df['timestamp'])
        greeks_df['timestamp'] = pd.to_datetime(greeks_df['timestamp'])
        
        # Merge on timestamp, symbol, strike, option_type
        merged = pd.merge(
            candles_df,
            greeks_df,
            on=['timestamp', 'symbol', 'expiry', 'strike', 'option_type'],
            how='inner'
        )
        
        logger.info(f"  Merged data: {len(merged)} rows")
        return merged
    
    def create_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Create additional features for neural training"""
        logger.info("Creating features...")
        
        # Technical indicators
        df['close_returns'] = df.groupby('symbol')['close'].pct_change()
        df['volume_ma5'] = df.groupby('symbol')['volume'].transform(lambda x: x.rolling(5).mean())
        df['iv_ma5'] = df.groupby('symbol')['iv'].transform(lambda x: x.rolling(5).mean())
        
        # Greeks momentum
        df['delta_momentum'] = df.groupby('symbol')['delta'].diff()
        df['gamma_momentum'] = df.groupby('symbol')['gamma'].diff()
        df['theta_momentum'] = df.groupby('symbol')['theta'].diff()
        
        # Premium changes
        df['premium_change'] = df.groupby('symbol')['premium'].pct_change()
        
        # Time-of-day features
        df['hour'] = df['timestamp'].dt.hour
        df['minute'] = df['timestamp'].dt.minute
        
        # Fill NaN values
        df = df.fillna(method='bfill').fillna(method='ffill')
        
        logger.info(f"  Features created: {len(df.columns)} columns")
        return df
    
    def normalize_data(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
        """Normalize data to 0-1 range for neural networks"""
        logger.info("Normalizing data...")
        
        normalization_params = {}
        
        # Columns to normalize
        numeric_cols = [
            'open', 'high', 'low', 'close', 'volume',
            'delta', 'gamma', 'theta', 'vega', 'premium', 'iv',
            'close_returns', 'premium_change'
        ]
        
        df_normalized = df.copy()
        
        for col in numeric_cols:
            if col in df.columns:
                min_val = df[col].min()
                max_val = df[col].max()
                
                # Avoid division by zero
                if max_val == min_val:
                    df_normalized[col] = 0
                else:
                    df_normalized[col] = (df[col] - min_val) / (max_val - min_val)
                
                normalization_params[col] = {
                    'min': float(min_val),
                    'max': float(max_val)
                }
        
        logger.info(f"  Normalized {len(numeric_cols)} columns")
        return df_normalized, normalization_params
    
    def create_sequences(self, df: pd.DataFrame, seq_length: int = 20) -> Tuple[np.ndarray, np.ndarray]:
        """
        Create sequences for LSTM training
        Input: Last 20 candles, Output: Next candle prediction
        """
        logger.info(f"Creating sequences (length={seq_length})...")
        
        sequences = []
        target_labels = []
        
        # Only use available columns
        feature_cols = [col for col in [
            'close', 'volume', 'delta', 'gamma', 'theta', 'vega',
            'premium', 'iv', 'close_returns', 'premium_change'
        ] if col in df.columns]
        
        logger.info(f"Using {len(feature_cols)} features: {feature_cols}")
        
        # Process each symbol efficiently
        for symbol in df['symbol'].unique():
            symbol_df = df[df['symbol'] == symbol].reset_index(drop=True).sort_values('timestamp')
            
            if len(symbol_df) < seq_length + 1:
                continue  # Skip if not enough data
            
            # Extract feature values
            feature_data = symbol_df[feature_cols].values
            
            for i in range(len(symbol_df) - seq_length):
                sequence = feature_data[i:i+seq_length]
                sequences.append(sequence)
                
                # Target: Did the price go up or down next candle?
                next_idx = i + seq_length
                if next_idx < len(symbol_df):
                    current_close = symbol_df['close'].iloc[i+seq_length-1]
                    next_close = symbol_df['close'].iloc[next_idx]
                    target = 1 if next_close > current_close else 0
                    target_labels.append(target)
        
        logger.info(f"  Created {len(sequences)} sequences")
        return np.array(sequences), np.array(target_labels)
    
    def save_prepared_data(self, 
                          df: pd.DataFrame, 
                          sequences: np.ndarray,
                          targets: np.ndarray,
                          normalization_params: Dict):
        """Save prepared data to disk"""
        logger.info("Saving prepared data...")
        
        # Save merged + featured data
        output_csv = self.output_dir / 'prepared_data.csv'
        df.to_csv(output_csv, index=False)
        logger.info(f"  ✓ {output_csv} ({len(df)} rows)")
        
        # Save sequences
        sequences_file = self.output_dir / 'sequences.npy'
        np.save(sequences_file, sequences)
        logger.info(f"  ✓ {sequences_file} ({sequences.shape})")
        
        # Save targets
        targets_file = self.output_dir / 'targets.npy'
        np.save(targets_file, targets)
        logger.info(f"  ✓ {targets_file} ({targets.shape})")
        
        # Save normalization parameters
        norm_file = self.output_dir / 'normalization_params.json'
        with open(norm_file, 'w') as f:
            json.dump(normalization_params, f, indent=2)
        logger.info(f"  ✓ {norm_file}")
        
        # Save data info
        info = {
            'total_rows': len(df),
            'sequences': int(sequences.shape[0]),
            'sequence_length': int(sequences.shape[1]),
            'features': int(sequences.shape[2]),
            'symbols': len(df['symbol'].unique()),
            'date_range': {
                'start': str(df['timestamp'].min()),
                'end': str(df['timestamp'].max())
            },
            'created_at': datetime.now().isoformat()
        }
        
        info_file = self.output_dir / 'data_info.json'
        with open(info_file, 'w') as f:
            json.dump(info, f, indent=2)
        logger.info(f"  ✓ {info_file}")
    
    def run_pipeline(self):
        """Run complete preparation pipeline"""
        logger.info(f"\n{'='*70}")
        logger.info("DATA PREPARATION PIPELINE")
        logger.info(f"{'='*70}\n")
        
        try:
            # Load data
            candles_df, greeks_df, trades_df = self.load_data()
            
            if len(candles_df) == 0 or len(greeks_df) == 0:
                logger.error("No data found. Run bulk_historical_data_collector.py first")
                return
            
            # Merge data
            merged_df = self.merge_data(candles_df, greeks_df)
            
            # Create features
            featured_df = self.create_features(merged_df)
            
            # Normalize
            normalized_df, norm_params = self.normalize_data(featured_df)
            
            # Create sequences
            sequences, targets = self.create_sequences(normalized_df)
            
            # Save
            self.save_prepared_data(normalized_df, sequences, targets, norm_params)
            
            logger.info(f"\n{'='*70}")
            logger.info("PIPELINE COMPLETE ✅")
            logger.info(f"Prepared data ready for neural ML training")
            logger.info(f"Output directory: {self.output_dir}")
            logger.info(f"{'='*70}\n")
            
        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)


def main():
    """Main entry point"""
    pipeline = DataPreparationPipeline()
    pipeline.run_pipeline()


if __name__ == '__main__':
    main()
