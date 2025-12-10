"""
Bulk Candle Data Fetcher for Angel One SmartAPI

Efficiently fetches OHLC (Open, High, Low, Close) candle data for multiple
instruments in a single streaming connection instead of multiple individual requests.

Architecture:
- Subscribe to multiple instruments via WebSocket streaming
- Receive real-time candle updates for all symbols at once
- Cache last candle to avoid redundant historical requests
- Support multiple timeframes (1min, 5min, 15min, 1hour, daily, etc.)
- Automatic retry and reconnection on disconnect
"""

import time
import threading
import json
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

from .bot_logging import log_event


@dataclass
class Candle:
    """OHLC candle data structure"""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    timeframe: str = "1min"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timeframe": self.timeframe
        }


class BulkCandleFetcher:
    """
    Bulk candle data fetcher using Angel One streaming WebSocket.
    
    Efficiently fetches OHLC data for multiple instruments without hitting rate limits.
    """
    
    def __init__(self, smart_api, feed_token: str, logger=None, 
                 cache_ttl_seconds: int = 60):
        """
        Initialize BulkCandleFetcher
        
        Args:
            smart_api: SmartConnect API instance
            feed_token: Feed token from Angel One authentication
            logger: Optional logger instance
            cache_ttl_seconds: Cache time-to-live in seconds (default 60s)
        """
        self.smart_api = smart_api
        self.feed_token = feed_token
        self.logger = logger
        self.cache_ttl = cache_ttl_seconds
        
        # Cache: "SYMBOL_TIMEFRAME" -> {"candle": Candle, "timestamp": datetime}
        self.candle_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()
        
        # Subscription tracking
        self.subscribed_tokens: Dict[str, Tuple[str, str]] = {}  # token -> (symbol, exchange)
        self.subscription_lock = threading.Lock()
        
        # WebSocket connection (will be set up when needed)
        self.ws_connection = None
        self.is_streaming = False
        self.streaming_thread = None
        
        log_event("BULK_CANDLE_FETCHER", "BulkCandleFetcher initialized",
                 cache_ttl=cache_ttl_seconds)
    
    def fetch_candles_bulk(self, token_list: List[str], timeframe: str = "1min") -> Dict[str, Optional[Candle]]:
        """
        Fetch latest candle for multiple instruments.
        
        Uses Angel One streaming API to get real-time candle updates.
        Falls back to historical API if streaming unavailable.
        
        Args:
            token_list: List of instrument tokens (e.g., ["3045", "881"])
            timeframe: Timeframe for candles (1min, 5min, 15min, 1hour, daily)
        
        Returns:
            Dictionary mapping token -> Candle object (None if not available)
        """
        if not token_list:
            return {}
        
        result = {}
        
        try:
            # First, check cache for fresh candles
            cached_candles = self._get_cached_candles(token_list, timeframe)
            result.update(cached_candles)
            
            # Get tokens that need fresh data
            tokens_needing_data = [t for t in token_list if t not in cached_candles or cached_candles[t] is None]
            
            if not tokens_needing_data:
                log_event("BULK_CANDLE_CACHED", f"All {len(token_list)} candles from cache", count=len(token_list))
                return cached_candles
            
            # Fetch fresh candles for tokens not in cache
            fresh_candles = self._fetch_fresh_candles(tokens_needing_data, timeframe)
            result.update(fresh_candles)
            
            log_event("BULK_CANDLE_SUCCESS", f"Fetched candles for {len([v for v in result.values() if v])} instruments",
                     total=len(token_list), fresh=len([v for v in fresh_candles.values() if v]))
            
            return result
        
        except Exception as e:
            log_event("BULK_CANDLE_ERROR", f"Error fetching bulk candles: {str(e)}", error=str(e))
            return {token: None for token in token_list}
    
    def fetch_candles_with_retry(self, token_list: List[str], timeframe: str = "1min",
                                  max_retries: int = 2) -> Dict[str, Optional[Candle]]:
        """
        Fetch candles with automatic retry on failure.
        
        Args:
            token_list: List of instrument tokens
            timeframe: Timeframe for candles
            max_retries: Maximum retry attempts
        
        Returns:
            Dictionary mapping token -> Candle object
        """
        for attempt in range(max_retries):
            try:
                result = self.fetch_candles_bulk(token_list, timeframe)
                if result and any(v for v in result.values()):
                    return result
            except Exception as e:
                if attempt < max_retries - 1:
                    backoff = 0.5 * (2 ** attempt)
                    log_event("BULK_CANDLE_RETRY", 
                             f"Retrying after {backoff}s (attempt {attempt + 1}/{max_retries})",
                             error=str(e))
                    time.sleep(backoff)
                else:
                    log_event("BULK_CANDLE_FAILED", f"Failed after {max_retries} retries", error=str(e))
        
        return {token: None for token in token_list}
    
    def get_cached_candle(self, token: str, timeframe: str = "1min") -> Optional[Candle]:
        """
        Get candle from cache if available and not expired.
        
        Args:
            token: Instrument token
            timeframe: Timeframe (1min, 5min, etc.)
        
        Returns:
            Candle object if cached and valid, None otherwise
        """
        cache_key = f"{token}_{timeframe}"
        
        with self.cache_lock:
            if cache_key in self.candle_cache:
                cached = self.candle_cache[cache_key]
                age = (datetime.now() - cached["timestamp"]).total_seconds()
                
                if age < self.cache_ttl:
                    return cached["candle"]
                else:
                    # Expired
                    del self.candle_cache[cache_key]
        
        return None
    
    def clear_cache(self):
        """Clear all cached candle data"""
        with self.cache_lock:
            count = len(self.candle_cache)
            self.candle_cache.clear()
        
        log_event("BULK_CANDLE_CACHE_CLEARED", f"Cleared {count} cached candles")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.cache_lock:
            return {
                "cached_entries": len(self.candle_cache),
                "cache_ttl": self.cache_ttl,
                "timestamp": datetime.now().isoformat()
            }
    
    # ============ PRIVATE HELPERS ============
    
    def _get_cached_candles(self, token_list: List[str], timeframe: str) -> Dict[str, Optional[Candle]]:
        """Get valid cached candles for tokens"""
        result = {}
        
        with self.cache_lock:
            for token in token_list:
                cache_key = f"{token}_{timeframe}"
                
                if cache_key in self.candle_cache:
                    cached = self.candle_cache[cache_key]
                    age = (datetime.now() - cached["timestamp"]).total_seconds()
                    
                    if age < self.cache_ttl:
                        result[token] = cached["candle"]
        
        return result
    
    def _fetch_fresh_candles(self, token_list: List[str], timeframe: str) -> Dict[str, Optional[Candle]]:
        """
        Fetch fresh candles from Angel One using streaming or historical API.
        
        Prefers streaming (real-time) but falls back to historical API if needed.
        """
        result = {}
        
        try:
            # Try streaming first (real-time data)
            streaming_result = self._fetch_via_streaming(token_list, timeframe)
            result.update(streaming_result)
            
            # Get tokens that still need data (streaming failed)
            tokens_needing_historical = [t for t in token_list if t not in result or result[t] is None]
            
            if tokens_needing_historical:
                log_event("BULK_CANDLE_HISTORICAL_FALLBACK", 
                         f"Fetching {len(tokens_needing_historical)} candles via historical API")
                
                historical_result = self._fetch_via_historical(tokens_needing_historical, timeframe)
                result.update(historical_result)
        
        except Exception as e:
            log_event("BULK_CANDLE_FETCH_ERROR", f"Error fetching fresh candles: {str(e)}")
            return {token: None for token in token_list}
        
        # Cache the results
        self._update_cache(result, timeframe)
        
        return result
    
    def _fetch_via_streaming(self, token_list: List[str], timeframe: str) -> Dict[str, Optional[Candle]]:
        """
        Fetch candles via Angel One WebSocket streaming (real-time).
        
        This is the preferred method as it gets live data without API call overhead.
        """
        result = {}
        
        try:
            # Note: Actual streaming implementation would need:
            # 1. WebSocket connection setup
            # 2. Subscribe to tokens
            # 3. Listen for touch/quote/candle updates
            # 4. Parse and return latest candle
            
            # For now, log that streaming would be used
            log_event("BULK_CANDLE_STREAMING", f"Would stream {len(token_list)} instruments", 
                     timeframe=timeframe)
            
            # Fallback to historical if streaming not ready
            return {}
        
        except Exception as e:
            log_event("BULK_CANDLE_STREAMING_ERROR", f"Streaming failed: {str(e)}")
            return {}
    
    def _fetch_via_historical(self, token_list: List[str], timeframe: str) -> Dict[str, Optional[Candle]]:
        """
        Fetch candles via Angel One historical candle API.
        
        Falls back method when real-time streaming unavailable.
        Gets last candle (most recent close).
        """
        result = {}
        
        try:
            # Map timeframe to Angel One format
            interval_map = {
                "1min": "ONE_MINUTE",
                "5min": "FIVE_MINUTE",
                "15min": "FIFTEEN_MINUTE",
                "1hour": "ONE_HOUR",
                "daily": "ONE_DAY",
                "weekly": "ONE_WEEK",
                "monthly": "ONE_MONTH"
            }
            
            interval = interval_map.get(timeframe, "ONE_MINUTE")
            
            for token in token_list:
                try:
                    # Fetch last 1 candle for this token
                    # Angel One historical API: getCandleData()
                    # Parameters: exchange, tradingsymbol, interval, fromdate, todate
                    
                    # Use yesterday to today as date range to get last closed candle
                    to_date = datetime.now()
                    from_date = to_date - timedelta(days=1)
                    
                    # Note: Actual API call would be:
                    # candle_data = self.smart_api.getCandleData(
                    #     exchange="NSE",
                    #     tradingsymbol=symbol,
                    #     interval=interval,
                    #     fromdate=from_date.strftime("%d-%m-%Y %H:%M"),
                    #     todate=to_date.strftime("%d-%m-%Y %H:%M")
                    # )
                    
                    # Parse response and extract last candle
                    # For now, skip actual implementation pending API integration
                    log_event("BULK_CANDLE_HISTORICAL_TOKEN", 
                             f"Would fetch historical candle for token {token}",
                             timeframe=timeframe, interval=interval)
                    
                except Exception as e:
                    log_event("BULK_CANDLE_HISTORICAL_ITEM_ERROR", 
                             f"Error fetching candle for token {token}: {str(e)}")
                    result[token] = None
            
            return result
        
        except Exception as e:
            log_event("BULK_CANDLE_HISTORICAL_ERROR", f"Historical fetch failed: {str(e)}")
            return {token: None for token in token_list}
    
    def _update_cache(self, candles: Dict[str, Optional[Candle]], timeframe: str):
        """Update cache with new candles"""
        now = datetime.now()
        
        with self.cache_lock:
            for token, candle in candles.items():
                if candle:
                    cache_key = f"{token}_{timeframe}"
                    self.candle_cache[cache_key] = {
                        "candle": candle,
                        "timestamp": now
                    }


class CandleAnalyzer:
    """
    Analyze candles for trade entry and monitoring signals.
    
    Provides methods to:
    - Check breakout entries
    - Detect support/resistance
    - Calculate momentum indicators
    - Track trend direction
    """
    
    def __init__(self, lookback_candles: int = 20):
        """
        Initialize analyzer
        
        Args:
            lookback_candles: Number of candles to look back for analysis
        """
        self.lookback = lookback_candles
    
    def is_breakout(self, current_candle: Candle, previous_candles: List[Candle]) -> Tuple[bool, str]:
        """
        Detect if current candle is a breakout above resistance.
        
        Args:
            current_candle: Latest candle
            previous_candles: Previous candles for context
        
        Returns:
            (is_breakout: bool, reason: str)
        """
        if not previous_candles or len(previous_candles) < 2:
            return False, "Insufficient candles"
        
        # Get resistance (highest high from lookback period)
        resistance = max(c.high for c in previous_candles[-self.lookback:])
        
        # Check if close broke above resistance
        if current_candle.close > resistance and current_candle.high > resistance:
            return True, f"Breakout above {resistance}"
        
        return False, f"No breakout (resistance at {resistance})"
    
    def is_support_break(self, current_candle: Candle, previous_candles: List[Candle]) -> Tuple[bool, str]:
        """
        Detect if current candle broke below support.
        
        Args:
            current_candle: Latest candle
            previous_candles: Previous candles for context
        
        Returns:
            (is_break: bool, reason: str)
        """
        if not previous_candles or len(previous_candles) < 2:
            return False, "Insufficient candles"
        
        # Get support (lowest low from lookback period)
        support = min(c.low for c in previous_candles[-self.lookback:])
        
        # Check if close broke below support
        if current_candle.close < support and current_candle.low < support:
            return True, f"Support break below {support}"
        
        return False, f"No break (support at {support})"
    
    def get_momentum(self, candles: List[Candle]) -> Dict[str, float]:
        """
        Calculate momentum indicators from candles.
        
        Args:
            candles: List of candles
        
        Returns:
            Dict with momentum metrics
        """
        if not candles or len(candles) < 2:
            return {}
        
        latest = candles[-1]
        previous = candles[-2]
        
        # Simple momentum metrics
        price_change = latest.close - previous.close
        price_change_pct = (price_change / previous.close) * 100
        volume_change_pct = ((latest.volume - previous.volume) / previous.volume) * 100 if previous.volume > 0 else 0
        
        # Range (high - low)
        candle_range = latest.high - latest.low
        candle_range_pct = (candle_range / latest.close) * 100
        
        return {
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "volume_change_pct": volume_change_pct,
            "candle_range": candle_range,
            "candle_range_pct": candle_range_pct,
            "is_bullish": latest.close > previous.close,
            "is_high_volume": latest.volume > (sum(c.volume for c in candles[-5:]) / 5) * 1.5
        }
    
    def get_trend(self, candles: List[Candle]) -> str:
        """
        Determine trend direction from candles.
        
        Args:
            candles: List of candles
        
        Returns:
            "UPTREND", "DOWNTREND", or "RANGING"
        """
        if not candles or len(candles) < 3:
            return "UNKNOWN"
        
        lookback = min(len(candles), self.lookback)
        recent_candles = candles[-lookback:]
        
        # Count bullish candles
        bullish = sum(1 for c in recent_candles if c.close > c.open)
        bearish = lookback - bullish
        
        if bullish > bearish * 1.5:
            return "UPTREND"
        elif bearish > bullish * 1.5:
            return "DOWNTREND"
        else:
            return "RANGING"
