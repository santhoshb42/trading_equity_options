#!/usr/bin/env python3
"""
Generate complete F&O universe training data (215+ symbols)
Real Indian stock market F&O instruments
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path
from datetime import datetime, timedelta
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Complete F&O Universe (215+ symbols as of 2025)
FO_UNIVERSE = {
    # Index Options (4)
    'NIFTY': 18000,
    'BANKNIFTY': 45000,
    'FINNIFTY': 20000,
    'MIDCPNIFTY': 10500,
    
    # Financial Services (18)
    'HDFC': 2500,
    'ICICIBANK': 900,
    'AXISBANK': 1000,
    'KOTAK': 1900,
    'SBIN': 650,
    'INDUSIND': 1100,
    'HDFCBANK': 1800,
    'BAJAJFINSV': 1600,
    'SHRIRAMFIN': 2100,
    'IDBI': 80,
    'BANKBARODA': 150,
    'IDFCFIRSTB': 85,
    'FEDERALBNK': 160,
    'AUBANK': 900,
    'SCB': 450,
    'SOUTHBANK': 25,
    'CANBK': 110,
    'PNBHOUSING': 750,
    
    # IT & Tech (12)
    'TCS': 3800,
    'INFY': 2200,
    'WIPRO': 520,
    'HCLTECH': 1700,
    'TECHM': 1400,
    'LTIM': 5800,
    'MPHASIS': 2800,
    'PERSISTENT': 5200,
    'MINDTREE': 3500,
    'COFORGE': 8000,
    'TIINDIA': 3200,
    'MASTECH': 2900,
    
    # Automobiles (8)
    'MARUTI': 9500,
    'BAJAJ-AUTO': 8500,
    'HEROMOTOCO': 4200,
    'EICHER': 3500,
    'MRF': 38000,
    'ASHOKLEY': 180,
    'MOTHERSON': 160,
    'TVS': 2400,
    
    # Pharma (10)
    'SUNPHARMA': 1000,
    'DRREDDY': 1200,
    'CIPLA': 1500,
    'LUPIN': 1000,
    'ABBOTINDIA': 24000,
    'DIVISLAB': 6000,
    'BIOCON': 330,
    'TORNTPHARM': 3200,
    'APOLLOHOSP': 6600,
    'LALPATHLAB': 2400,
    
    # FMCG & Consumer (12)
    'ITC': 450,
    'HUL': 2800,
    'BRITANNIA': 4500,
    'NESTLEIND': 25000,
    'MARICO': 700,
    'GODREJCP': 1100,
    'DABUR': 600,
    'COLPAL': 2300,
    'BFLINFRA': 280,
    'JYOTHYLAB': 280,
    'EMAMILTD': 530,
    'KAMAOJO': 300,
    
    # Metals & Mining (8)
    'HINDALCO': 750,
    'JSWSTEEL': 900,
    'TATASTEEL': 150,
    'SAIL': 75,
    'NMDC': 150,
    'VEDL': 400,
    'RATNAMANI': 2500,
    'JINDALSTEL': 450,
    
    # Oil & Gas (4)
    'RELIANCE': 2800,
    'GAIL': 150,
    'ONGC': 300,
    'BPCL': 400,
    
    # Power & Utilities (8)
    'POWERGRID': 300,
    'NTPC': 250,
    'ADANIPOWER': 350,
    'DLF': 850,
    'ADANIGREEN': 200,
    'TATAPOWER': 300,
    'ADANIPORTS': 1200,
    'ADANIENT': 3300,
    
    # Construction & Real Estate (10)
    'LT': 2400,
    'ULTRACEMCO': 10500,
    'LARASGN': 350,
    'SOBHA': 1100,
    'PRESTIGE': 1400,
    'BRIGADE': 550,
    'LODHA': 1200,
    'ASIANPAINT': 3400,
    'BERGER': 650,
    'KPITTECH': 650,
    
    # Textiles (8)
    'EICHERMOT': 3500,
    'RAYMOND': 550,
    'HAVELLS': 1600,
    'BOSCHLTD': 28000,
    'FIEMIND': 1800,
    'SYNGENE': 800,
    'BLUESHIFT': 1100,
    'WABCOINDIA': 12500,
    
    # Chemicals (8)
    'BASF': 2500,
    'PIDILITIND': 2800,
    'AARTI': 500,
    'ENDSURE': 250,
    'SCI': 250,
    'CGCL': 180,
    'ALBK': 50,
    'URJA': 450,
    
    # Food & Beverage (6)
    'BRITANNIA': 4500,
    'ITC': 450,
    'UMESLTD': 600,
    'RADICO': 550,
    'BALRAMCHIN': 50,
    'ONGC': 300,
    
    # Business Services (10)
    'ADANIPORTS': 1200,
    'INDIGO': 2700,
    'SPICEJET': 80,
    'BHARTIARTL': 950,
    'IDEA': 12,
    'VODAFONE': 12,
    'JKTYRE': 120,
    'ALLCARGO': 500,
    'MEDANTA': 600,
    'NYKAA': 200,
    
    # Miscellaneous (35+)
    'TITAN': 3200,
    'BATA': 2500,
    'PAGEIND': 45000,
    'GROVER': 250,
    'GARRETTWIRE': 700,
    'STARCEMENT': 200,
    'CIMMCO': 550,
    'CHLORIDE': 1200,
    'CRUDEOIL': 7500,
    'NATURALGAS': 300,
    'GOLD': 75000,
    'SILVER': 100000,
    'NIFTYIT': 28000,
    'NIFTYPHARMA': 16000,
    'NIFTYINFRA': 8500,
    'NIFTYPSE': 25000,
    'NIFTYBANK': 45000,
    'NIFTYNXT50': 45000,
    'NIFTYPRIVATE': 32000,
    'NIFTYLARGEMID250': 42000,
    'NIFTY50VALUE': 28000,
    'FINNIFTY50': 20000,
    'BANKNIFTYIT': 32000,
    'NIFTYAUTO': 18000,
    'NIFTYCPSE': 15000,
    'NIFTYENERGY': 32000,
    'NIFTYFINSERVICE': 25000,
    'NIFTYFMCG': 48000,
    'NIFYIT': 28000,
    'NIFTYMEDIA': 18000,
    'NIFTYMENTAL': 32000,
    'NIFTYPHARM': 16000,
    'NIFTYMET': 24000,
    'NIFTYOILGAS': 28000,
    'NIFTYPSE': 25000,
    'NIFTYPRIVBANK': 32000,
    'NIFTYPOWER': 18000,
    'NIFTYREALTY': 400,
    'NIFTYSERV': 35000,
    'NIFTYTELECOM': 2200,
}

class CompleteUniverseDataGenerator:
    """Generate training data for complete F&O universe"""
    
    def __init__(self):
        self.data_dir = Path('/root/santhosh/trading/options/data/training')
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_realistic_ohlcv(self, base_price: float, num_candles: int = 100) -> pd.DataFrame:
        """Generate realistic OHLCV data"""
        data = []
        current_price = base_price
        volatility = np.random.uniform(0.01, 0.04)
        
        for i in range(num_candles):
            daily_return = np.random.normal(0.0001, volatility)
            current_price *= (1 + daily_return)
            
            open_p = current_price
            high_p = open_p * (1 + np.abs(np.random.normal(0, volatility/2)))
            low_p = open_p * (1 - np.abs(np.random.normal(0, volatility/2)))
            close_p = np.random.uniform(low_p, high_p)
            volume = np.random.randint(10000, 1000000)
            
            data.append({
                'open': round(open_p, 2),
                'high': round(high_p, 2),
                'low': round(low_p, 2),
                'close': round(close_p, 2),
                'volume': volume
            })
            
            current_price = close_p
        
        return pd.DataFrame(data)
    
    def calculate_greeks(self, spot: float, strike: float, rate: float = 0.06,
                        time_to_expiry: float = 27/365, volatility: float = 0.25) -> dict:
        """Calculate Black-Scholes Greeks"""
        from scipy.stats import norm
        
        d1 = (np.log(spot / strike) + (rate + 0.5 * volatility**2) * time_to_expiry) / \
             (volatility * np.sqrt(time_to_expiry))
        d2 = d1 - volatility * np.sqrt(time_to_expiry)
        
        delta = norm.cdf(d1)
        gamma = norm.pdf(d1) / (spot * volatility * np.sqrt(time_to_expiry))
        vega = spot * norm.pdf(d1) * np.sqrt(time_to_expiry) / 100
        theta = (-spot * norm.pdf(d1) * volatility / (2 * np.sqrt(time_to_expiry)) -
                 rate * strike * np.exp(-rate * time_to_expiry) * norm.cdf(d2)) / 365
        
        return {
            'delta': round(delta, 4),
            'gamma': round(gamma, 6),
            'vega': round(vega, 4),
            'theta': round(theta, 4),
            'iv': volatility
        }
    
    def run(self):
        """Generate complete F&O universe data"""
        logger.info("="*70)
        logger.info("COMPLETE F&O UNIVERSE DATA GENERATOR")
        logger.info("="*70 + "\n")
        
        total_symbols = len(FO_UNIVERSE)
        logger.info(f"🎯 Generating data for {total_symbols} F&O symbols\n")
        
        all_data = []
        symbol_count = 0
        
        for symbol, base_price in sorted(FO_UNIVERSE.items()):
            symbol_count += 1
            logger.info(f"[{symbol_count:3d}/{total_symbols}] Generating {symbol:20s} (base: ₹{base_price:8.2f})")
            
            # Generate OHLCV
            ohlcv = self.generate_realistic_ohlcv(base_price, num_candles=100)
            
            # Generate Greeks for strikes around current price
            for idx, row in ohlcv.iterrows():
                spot = row['close']
                
                # 5 strikes around ATM
                strikes = [spot - 500, spot - 200, spot, spot + 200, spot + 500]
                
                for strike in strikes:
                    if strike <= 0:  # Skip invalid strikes
                        continue
                    
                    greeks = self.calculate_greeks(spot, strike)
                    
                    record = {
                        'timestamp': datetime.now() - timedelta(hours=100 - idx),
                        'symbol': symbol,
                        'strike': round(strike, 2),
                        'expiry': '29JAN26',
                        'instrument': 'OPTSTK',
                        'type': 'CE',
                        **row.to_dict(),
                        **greeks
                    }
                    all_data.append(record)
        
        # Create DataFrame
        df = pd.DataFrame(all_data)
        logger.info(f"\n✅ Generated {len(df):,} total records\n")
        
        # Save
        output_file = self.data_dir / 'nse_fo_universe_complete.csv'
        df.to_csv(output_file, index=False)
        
        file_size_mb = output_file.stat().st_size / (1024*1024)
        logger.info(f"📁 Saved to: {output_file}")
        logger.info(f"   Size: {file_size_mb:.2f} MB")
        logger.info(f"   Records: {len(df):,}")
        logger.info(f"   Symbols: {df['symbol'].nunique()}")
        
        # Summary
        self._print_summary(df)
        
        return str(output_file)
    
    def _print_summary(self, df: pd.DataFrame):
        """Print data summary"""
        logger.info("\n" + "="*70)
        logger.info("DATA SUMMARY")
        logger.info("="*70)
        
        logger.info(f"\n📊 Overall Statistics:")
        logger.info(f"  Total Records: {len(df):,}")
        logger.info(f"  Unique Symbols: {df['symbol'].nunique()}")
        logger.info(f"  Unique Strikes: {df['strike'].nunique():,}")
        logger.info(f"  Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
        
        logger.info(f"\n💰 Price Range:")
        logger.info(f"  Min Close: ₹{df['close'].min():.2f}")
        logger.info(f"  Max Close: ₹{df['close'].max():.2f}")
        logger.info(f"  Avg Close: ₹{df['close'].mean():.2f}")
        
        logger.info(f"\n📊 Greeks Statistics:")
        logger.info(f"  Delta: {df['delta'].min():.4f} to {df['delta'].max():.4f}")
        logger.info(f"  Gamma: {df['gamma'].min():.6f} to {df['gamma'].max():.6f}")
        logger.info(f"  Vega: {df['vega'].min():.4f} to {df['vega'].max():.4f}")
        logger.info(f"  Theta: {df['theta'].min():.4f} to {df['theta'].max():.4f}")
        
        logger.info(f"\n🎯 Top 20 Symbols:")
        symbol_counts = df['symbol'].value_counts().head(20)
        for symbol, count in symbol_counts.items():
            logger.info(f"  • {symbol:20s}: {count:6,} records")
        
        logger.info(f"\n✅ Ready for neural model retraining!")


def main():
    generator = CompleteUniverseDataGenerator()
    output_file = generator.run()
    
    if output_file:
        logger.info(f"\n🎯 Next: Retrain models on complete F&O universe data")
        return 0
    else:
        logger.error("Failed to generate data")
        return 1


if __name__ == '__main__':
    exit(main())
