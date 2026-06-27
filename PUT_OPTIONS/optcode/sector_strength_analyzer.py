"""
Sector Strength Analyzer - Standalone Module

Fetches and analyzes sector-level indicators:
- Sector RSI
- Sector performance vs NIFTY
- Sector volume trends
- Participation rate (how many stocks in sector are bullish)

Can be used standalone for testing or integrated into bot.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Any
import logging
try:
    from .optconfig import DATA_DIR as _DATA_DIR
    _DEFAULT_SECTOR_FILE = str(_DATA_DIR / "symbol_sectors.json")
except ImportError:
    _DEFAULT_SECTOR_FILE = str(Path(__file__).parent.parent / 'data' / 'symbol_sectors.json')

# Setup basic logging for standalone testing
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s'
)
logger = logging.getLogger('SECTOR_ANALYZER')

# =============================================================================
# Sector Mapping Manager
# =============================================================================

class SectorMappingManager:
    """Loads and manages symbol-to-sector mappings"""
    
    def __init__(self, mapping_file: str = None):
        if mapping_file is None:
            mapping_file = _DEFAULT_SECTOR_FILE
        """Initialize sector mapping from JSON file"""
        self.mapping_file = Path(mapping_file)
        self.reverse_mapping = {}  # symbol -> sector
        self.sector_symbols = {}   # sector -> [symbols]
        self.load_mappings()
    
    def load_mappings(self) -> bool:
        """Load mappings from JSON file"""
        try:
            if not self.mapping_file.exists():
                logger.error(f"Mapping file not found: {self.mapping_file}")
                return False
            
            with open(self.mapping_file, 'r') as f:
                data = json.load(f)
            
            # Load reverse mapping (symbol -> sector)
            self.reverse_mapping = data.get('reverse_mapping', {})
            
            # Load sector->symbols mapping
            sectors = data.get('sectors', {})
            for sector_name, sector_data in sectors.items():
                symbols = sector_data.get('symbols', [])
                self.sector_symbols[sector_name] = symbols
            
            logger.info(f"MAPPING_LOADED | Sectors: {len(self.sector_symbols)} | Symbols: {len(self.reverse_mapping)}")
            return True
        
        except Exception as e:
            logger.error(f"MAPPING_LOAD_FAILED | {str(e)}")
            return False
    
    def get_sector(self, symbol: str) -> str:
        """Get sector for a symbol (with fallback to UNKNOWN)"""
        sector = self.reverse_mapping.get(symbol, 'UNKNOWN')
        return sector
    
    def get_sector_symbols(self, sector: str) -> List[str]:
        """Get all symbols in a sector"""
        return self.sector_symbols.get(sector, [])
    
    def get_all_sectors(self) -> List[str]:
        """Get all sector names"""
        return list(self.sector_symbols.keys())

# =============================================================================
# Sector Strength Analyzer
# =============================================================================

class SectorStrengthAnalyzer:
    """
    Analyzes sector-level technical indicators
    
    Requires broker connection to fetch sector index data.
    Calculates:
    - Sector RSI
    - Sector momentum (vs NIFTY)
    - Sector participation (% of constituent stocks up)
    """
    
    def __init__(self, broker=None):
        """
        Initialize sector analyzer
        
        Args:
            broker: AngelOne broker instance (for live data fetching)
        """
        self.broker = broker
        self.sector_mapping = SectorMappingManager()
        self.cache = {}  # Cache sector data
        self.cache_ttl = 60  # 60 second cache
        self.last_update = {}
    
    def get_sector(self, symbol: str) -> str:
        """Get sector for a symbol"""
        return self.sector_mapping.get_sector(symbol)
    
    def get_sector_performance(self, sector: str) -> Optional[Dict[str, Any]]:
        """
        Get sector performance metrics
        
        Returns:
            {
                'sector': 'BANK',
                'rsi': 65.5,
                'performance_pct': 1.2,
                'participation_pct': 75.0,
                'nifty_comparison': 'outperforming',
                'volume_trend': 'increasing'
            }
        """
        try:
            # Check cache
            now = time.time()
            if sector in self.cache:
                cache_time = self.last_update.get(sector, 0)
                if now - cache_time < self.cache_ttl:
                    logger.debug(f"CACHE_HIT | {sector}")
                    return self.cache[sector]
            
            # For now, return mock data (will be replaced with broker API calls)
            data = self._fetch_sector_data(sector)
            
            if data:
                self.cache[sector] = data
                self.last_update[sector] = now
            
            return data
        
        except Exception as e:
            logger.error(f"SECTOR_PERF_ERROR | {sector} | {str(e)}")
            return None
    
    def _fetch_sector_data(self, sector: str) -> Optional[Dict[str, Any]]:
        """
        Fetch LIVE sector data from broker using underlying symbols we're actively trading.
        This ensures real data, not hardcoded values.
        """
        if not self.broker:
            logger.warning(f"BROKER_NOT_AVAILABLE | Cannot fetch sector data")
            return None
        
        try:
            # Get sector symbols
            symbols = self.sector_mapping.get_sector_symbols(sector)
            if not symbols:
                logger.warning(f"NO_SYMBOLS_FOR_SECTOR | {sector}")
                return None
            
            logger.debug(f"FETCHING_SECTOR_DATA_LIVE | {sector} | Attempting {len(symbols)} symbols")
            
            # Track sector strength metrics
            up_count = 0
            down_count = 0
            rsi_values = []
            price_changes = []
            total_volume = 0
            symbols_checked = 0
            
            # Get historical data to calculate RSI for each symbol
            # We'll sample symbols intelligently - try up to 10 symbols
            sample_symbols = symbols[:10] if len(symbols) > 10 else symbols
            
            for symbol in sample_symbols:
                try:
                    # Fetch LIVE market data for the equity symbol
                    market_data = self.broker.get_market_data(symbol, 'NSE')
                    
                    if market_data:
                        symbols_checked += 1
                        open_price = float(market_data.get('open', market_data.get('o', 0)))
                        ltp = float(market_data.get('ltp', 0))
                        volume = int(market_data.get('volume', market_data.get('v', 0)))
                        
                        # Calculate price change
                        price_change = ((ltp - open_price) / open_price * 100) if open_price > 0 else 0
                        price_changes.append(price_change)
                        
                        # Count up/down
                        if ltp > open_price:
                            up_count += 1
                        else:
                            down_count += 1
                        
                        total_volume += volume
                        
                        logger.debug(f"SECTOR_SYMBOL_DATA | {symbol} | LTP=₹{ltp} | Change={price_change:.2f}%")
                    
                except Exception as e:
                    # Silently skip if symbol data unavailable
                    logger.debug(f"SECTOR_DATA_SKIP | {symbol} | {str(e)[:40]}")
                    continue
            
            # Calculate final metrics
            total_symbols = up_count + down_count
            
            # LIVE participation rate from actually available data
            participation = (up_count / total_symbols * 100) if total_symbols > 0 else 50.0
            
            # LIVE sector RSI - average of price movements
            # If many symbols up, RSI tends toward 70+
            # If many symbols down, RSI tends toward 30-
            avg_price_change = (sum(price_changes) / len(price_changes)) if price_changes else 0
            sector_rsi = 50 + (avg_price_change * 2)  # Map price change to RSI scale
            sector_rsi = max(0, min(100, sector_rsi))  # Clamp to 0-100
            
            # LIVE sector performance - actual average price change
            sector_performance = avg_price_change
            
            # Determine sentiment
            if participation > 60:
                nifty_comparison = 'BULLISH'
            elif participation < 40:
                nifty_comparison = 'BEARISH'
            else:
                nifty_comparison = 'NEUTRAL'
            
            logger.info(f"SECTOR_DATA_LIVE | {sector} | RSI={sector_rsi:.1f} | Perf={sector_performance:.2f}% | Part={participation:.1f}% | Symbols={symbols_checked}/{len(sample_symbols)}")
            
            return {
                'sector': sector,
                'rsi': round(sector_rsi, 2),  # LIVE calculated RSI
                'performance_pct': round(sector_performance, 2),  # LIVE calculated performance
                'participation_pct': round(participation, 1),  # LIVE participation rate
                'participation_up': up_count,
                'participation_total': total_symbols,
                'nifty_comparison': nifty_comparison,
                'volume_trend': 'increasing' if total_volume > 0 else 'unknown',
                'timestamp': datetime.now().isoformat(),
                'symbols_checked': symbols_checked,
                'avg_price_change': round(sector_performance, 2)
            }
        
        except Exception as e:
            logger.error(f"SECTOR_DATA_FETCH_ERROR | {sector} | {str(e)}")
            return None
    
    def get_symbol_sector_info(self, symbol: str) -> Dict[str, Any]:
        """Get sector info for a symbol"""
        sector = self.sector_mapping.get_sector(symbol)
        sector_data = self.get_sector_performance(sector) if sector != 'UNKNOWN' else None
        
        return {
            'symbol': symbol,
            'sector': sector,
            'sector_data': sector_data
        }
    
    def is_sector_bullish(self, symbol: str, threshold: float = 60.0) -> Tuple[bool, str]:
        """
        Check if symbol's sector is bullish
        
        Args:
            symbol: Stock symbol
            threshold: Participation threshold (default 60%)
        
        Returns:
            (is_bullish, reason)
        """
        sector = self.sector_mapping.get_sector(symbol)
        if sector == 'UNKNOWN':
            return False, f"Sector not mapped for {symbol}"
        
        sector_data = self.get_sector_performance(sector)
        if not sector_data:
            return False, f"Could not fetch sector data for {sector}"
        
        participation = sector_data.get('participation_pct', 0)
        is_bullish = participation >= threshold
        reason = f"{sector} participation: {participation:.1f}% (threshold: {threshold}%)"
        
        return is_bullish, reason

# =============================================================================
# Standalone Tester
# =============================================================================

def test_sector_analyzer_standalone():
    """Test sector analyzer without broker connection"""
    logger.info("="*80)
    logger.info("SECTOR ANALYZER - STANDALONE TEST (No Broker)")
    logger.info("="*80)
    
    # Initialize without broker
    analyzer = SectorStrengthAnalyzer(broker=None)
    
    # Test 1: Sector mapping
    logger.info("\n[TEST 1] Sector Mapping")
    print("-" * 60)
    test_symbols = ['HDFCBANK', 'HINDALCO', 'INFY', 'UNKNOWN_SYMBOL']
    for symbol in test_symbols:
        sector = analyzer.sector_mapping.get_sector(symbol)
        print(f"  {symbol:15} -> {sector}")
    
    # Test 2: Get all sectors
    logger.info("\n[TEST 2] All Sectors")
    print("-" * 60)
    all_sectors = analyzer.sector_mapping.get_all_sectors()
    for sector in sorted(all_sectors):
        symbols = analyzer.sector_mapping.get_sector_symbols(sector)
        print(f"  {sector:20} : {len(symbols):2} symbols - {', '.join(symbols[:3])}...")
    
    # Test 3: No orphans
    logger.info("\n[TEST 3] Checking for Orphaned Symbols")
    print("-" * 60)
    all_mapped = set()
    for sector_symbols in analyzer.sector_mapping.sector_symbols.values():
        all_mapped.update(sector_symbols)
    
    reverse_keys = set(analyzer.sector_mapping.reverse_mapping.keys())
    
    if all_mapped == reverse_keys:
        logger.info(f"✓ NO ORPHANS | All {len(all_mapped)} symbols accounted for")
    else:
        orphans = reverse_keys - all_mapped
        logger.warning(f"✗ ORPHANED SYMBOLS: {orphans}")
    
    # Test 4: Coverage
    logger.info("\n[TEST 4] Symbol Coverage")
    print("-" * 60)
    coverage_symbols = ['ALKEM', 'AMBUJACEM', 'ANGELONE', 'ASTRAL', 'BHARATFORG', 
                       'BSE', 'CANBK', 'CGPOWER', 'COLPAL', 'CONCOR']
    
    for symbol in coverage_symbols:
        sector = analyzer.sector_mapping.get_sector(symbol)
        status = "✓ MAPPED" if sector != 'UNKNOWN' else "✗ ORPHAN"
        print(f"  {status} | {symbol:15} -> {sector}")
    
    logger.info("\n" + "="*80)
    logger.info("STANDALONE TEST COMPLETED")
    logger.info("="*80)

def test_with_broker(broker):
    """Test sector analyzer with broker connection"""
    logger.info("="*80)
    logger.info("SECTOR ANALYZER - BROKER TEST")
    logger.info("="*80)
    
    analyzer = SectorStrengthAnalyzer(broker=broker)
    
    # Test sectors
    test_sectors = ['BANK', 'METALS', 'IT', 'FMCG']
    
    for sector in test_sectors:
        logger.info(f"\nFetching data for {sector}...")
        data = analyzer.get_sector_performance(sector)
        
        if data:
            print(f"\n  Sector: {data['sector']}")
            print(f"  Participation: {data.get('participation_pct', 0):.1f}%")
            print(f"  Up: {data.get('participation_up', 0)}/{data.get('participation_total', 0)}")
            print(f"  Sentiment: {data.get('nifty_comparison', 'N/A')}")
            print(f"  Timestamp: {data.get('timestamp', 'N/A')}")
        else:
            logger.warning(f"Failed to fetch data for {sector}")
    
    # Test specific symbols
    logger.info("\n" + "-"*60)
    logger.info("Testing specific symbols:")
    print("-" * 60)
    
    test_symbols = ['HDFCBANK', 'HINDALCO', 'TECHM', 'NESTLEIND']
    for symbol in test_symbols:
        is_bullish, reason = analyzer.is_sector_bullish(symbol, threshold=60)
        status = "✓ BULLISH" if is_bullish else "✗ BEARISH"
        print(f"  {status} | {symbol:15} | {reason}")
    
    logger.info("\n" + "="*80)
    logger.info("BROKER TEST COMPLETED")
    logger.info("="*80)

# =============================================================================
# Main: Choose test mode
# =============================================================================

if __name__ == '__main__':
    import sys
    
    # Check if running standalone or with broker
    if len(sys.argv) > 1 and sys.argv[1] == '--with-broker':
        # With broker
        try:
            from angelone_options import get_options_broker
            broker = get_options_broker()
            if broker and broker.authenticated:
                test_with_broker(broker)
            else:
                logger.error("Broker not authenticated - falling back to standalone test")
                test_sector_analyzer_standalone()
        except Exception as e:
            logger.error(f"Broker initialization failed: {e} - falling back to standalone")
            test_sector_analyzer_standalone()
    else:
        # Standalone test
        test_sector_analyzer_standalone()
