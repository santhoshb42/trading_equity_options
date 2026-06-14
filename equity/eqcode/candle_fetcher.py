"""
Candle Data Fetcher for Angel One SmartAPI
Supports: Equity (NSE), Options (NFO), Futures, Commodities, Currency
"""

import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import time

logger = logging.getLogger(__name__)


class CandleFetcher:
    """
    Fetch OHLCV candle data from Angel One Historical API
    Works for both equity and options
    """
    
    def __init__(self, smart_api, cache_ttl_seconds: int = 300):
        """
        Args:
            smart_api: SmartConnect instance (already authenticated)
            cache_ttl_seconds: Cache TTL for candle data (default 5 min)
        """
        self.smart_api = smart_api
        self.cache_ttl = cache_ttl_seconds
        self.cache = {}
        self.cache_timestamps = {}
    
    def fetch_candles(
        self,
        exchange: str,
        token: str,
        interval: str,
        from_date: str,
        to_date: str,
        use_cache: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        Fetch candles from Angel One Historical API
        
        Args:
            exchange: "NSE" (equity), "NFO" (options/futures), "MCX", "CDS"
            token: Instrument token (string)
            interval: "ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE", "ONE_HOUR", "ONE_DAY"
            from_date: ISO format "2024-02-01 09:15"
            to_date: ISO format "2024-02-10 15:30"
            use_cache: Use cached data if available
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        
        cache_key = f"{exchange}:{token}:{interval}:{from_date}:{to_date}"
        
        # Check cache
        if use_cache and cache_key in self.cache:
            if time.time() - self.cache_timestamps[cache_key] < self.cache_ttl:
                logger.debug(f"Cache hit: {cache_key}")
                return self.cache[cache_key].copy()
        
        try:
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_date,
                "todate": to_date
            }
            
            logger.debug(f"Fetching candles: {params}")
            response = self.smart_api.getCandleData(params)
            
            if not response or response.get('status') != 'success':
                logger.error(f"Failed to fetch candles: {response}")
                return None
            
            data = response.get('data', [])
            if not data:
                logger.warning(f"No candle data returned for {cache_key}")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(data)
            
            # Rename columns if needed
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            # Convert OHLCV to numeric
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Sort by timestamp
            df = df.sort_values('timestamp').reset_index(drop=True)
            
            # Cache it
            self.cache[cache_key] = df.copy()
            self.cache_timestamps[cache_key] = time.time()
            
            logger.info(f"Fetched {len(df)} candles for {cache_key}")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching candles: {e}")
            return None
    
    def fetch_latest_candles(
        self,
        exchange: str,
        token: str,
        interval: str,
        num_candles: int = 100
    ) -> Optional[pd.DataFrame]:
        """
        Fetch latest N candles (convenience method)
        
        Args:
            exchange: "NSE", "NFO", etc.
            token: Instrument token
            interval: Candle interval
            num_candles: Number of recent candles to fetch
        
        Returns:
            DataFrame with latest candles
        """
        
        # Calculate date range based on interval
        to_date = datetime.now()
        
        # Estimate from_date based on interval
        interval_map = {
            "ONE_MINUTE": 1,
            "THREE_MINUTE": 3,
            "FIVE_MINUTE": 5,
            "FIFTEEN_MINUTE": 15,
            "THIRTY_MINUTE": 30,
            "ONE_HOUR": 60,
            "ONE_DAY": 1440
        }
        
        minutes = interval_map.get(interval, 5)
        from_date = to_date - timedelta(minutes=minutes * num_candles * 1.5)
        
        return self.fetch_candles(
            exchange=exchange,
            token=token,
            interval=interval,
            from_date=from_date.strftime("%Y-%m-%d %H:%M"),
            to_date=to_date.strftime("%Y-%m-%d %H:%M")
        )
    
    def fetch_candles_bulk(
        self,
        tokens_list: List[Tuple[str, str]],
        interval: str,
        from_date: str,
        to_date: str
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch candles for multiple tokens (sequential, respects rate limits)
        
        Args:
            tokens_list: List of (exchange, token) tuples
            interval: Candle interval
            from_date: Start date
            to_date: End date
        
        Returns:
            Dict mapping "exchange:token" -> DataFrame
        """
        results = {}
        
        for exchange, token in tokens_list:
            try:
                df = self.fetch_candles(exchange, token, interval, from_date, to_date)
                results[f"{exchange}:{token}"] = df
                time.sleep(0.1)  # Small delay to respect rate limits
            except Exception as e:
                logger.error(f"Error fetching {exchange}:{token}: {e}")
                results[f"{exchange}:{token}"] = None
        
        return results
    
    def clear_cache(self):
        """Clear all cached candles"""
        self.cache.clear()
        self.cache_timestamps.clear()
        logger.info("Candle cache cleared")
    
    def get_cache_stats(self) -> Dict:
        """Get cache statistics"""
        return {
            "cached_keys": len(self.cache),
            "cache_size_mb": sum(
                df.memory_usage(deep=True).sum() / 1024**2 
                for df in self.cache.values() if df is not None
            ),
            "oldest_cache_age_sec": min(
                (time.time() - ts for ts in self.cache_timestamps.values()),
                default=0
            )
        }
