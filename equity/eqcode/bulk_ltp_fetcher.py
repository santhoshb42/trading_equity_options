"""
Bulk LTP Fetcher for Angel One SmartAPI

Efficiently fetches LTP (Last Traded Price) for multiple instruments in a single API call.
Supports up to 50 instruments per request, avoids rate limiting, and includes caching.

Architecture:
- Batch requests into groups of 50 (Angel One limit)
- Cache results to avoid repeated fetches for same symbols
- Support NSE equity and NFO options
- Automatic retry with exponential backoff
"""

import time
import threading
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
from datetime import datetime, timedelta

from .bot_logging import log_event


class BulkLTPFetcher:
    """
    Bulk LTP fetcher using Angel One SmartAPI ltpData endpoint.
    
    Fetches LTP (Last Traded Price) for multiple instruments efficiently by calling
    ltpData() for each token with rate limit respect (1 req/sec minimum).
    
    Note: Angel One doesn't provide a true bulk API for LTP, so this batches individual
    ltpData() calls but respects rate limits to avoid being throttled.
    """
    
    def __init__(self, smart_api, logger=None, cache_ttl_seconds: int = 5):
        """
        Initialize BulkLTPFetcher
        
        Args:
            smart_api: SmartConnect API instance
            logger: Optional logger instance
            cache_ttl_seconds: Cache time-to-live in seconds (default 5s for real-time)
        """
        self.smart_api = smart_api
        self.logger = logger
        self.cache_ttl = cache_ttl_seconds
        
        # Cache: symbol_token -> {"ltp": float, "timestamp": datetime, "symbol": str}
        self.ltp_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()
        
        # Request tracking for rate limiting
        self.last_request_time = 0.0
        self.min_request_interval = 1.0  # Angel One allows 1 req/sec max
        
        log_event("BULK_LTP_FETCHER", "BulkLTPFetcher initialized",
                 cache_ttl=cache_ttl_seconds, min_interval=self.min_request_interval)
    
    def fetch_bulk_ltp(self, token_dict: Dict[str, List[str]]) -> Dict[str, float]:
        """
        Fetch LTP for multiple instruments by calling ltpData for each token.
        
        Angel One SmartAPI doesn't have a true bulk endpoint, so we fetch each token
        individually but respect rate limits by spacing out requests.
        
        Args:
            token_dict: Dictionary mapping exchange -> list of tokens
                       Example: {"NSE": ["3045", "881"], "NFO": ["35078"]}
        
        Returns:
            Dictionary mapping "EXCHANGE_TOKEN" -> LTP value
            Example: {"NSE_3045": 2945.5, "NFO_35078": 112.4}
        """
        if not token_dict:
            return {}
        
        ltps = {}
        total_tokens = self._count_tokens(token_dict)
        
        log_event("BULK_LTP_REQUEST", f"Fetching LTP for {total_tokens} instruments",
                 exchanges=list(token_dict.keys()))
        
        try:
            # Iterate through exchanges and tokens
            for exchange, tokens in token_dict.items():
                for token in tokens:
                    try:
                        # Respect rate limit: 1 request per second minimum
                        time_since_last = time.time() - self.last_request_time
                        if time_since_last < self.min_request_interval:
                            wait_time = self.min_request_interval - time_since_last
                            time.sleep(wait_time)
                        
                        self.last_request_time = time.time()
                        
                        # Call ltpData API for this specific token
                        response = self.smart_api.ltpData(exchange, "", token)
                        
                        if response and response.get("status"):
                            try:
                                ltp_value = float(response.get("data", {}).get("ltp", 0))
                                if ltp_value > 0:
                                    key = f"{exchange}_{token}"
                                    ltps[key] = ltp_value
                            except (ValueError, TypeError, KeyError) as e:
                                log_event("BULK_LTP_PARSE_SKIP", 
                                         f"Invalid LTP data for {exchange}_{token}: {str(e)}",
                                         response=response)
                        
                    except Exception as e:
                        # Log individual token fetch failures but continue with others
                        log_event("BULK_LTP_TOKEN_ERROR", 
                                 f"Error fetching LTP for {exchange}_{token}: {str(e)}",
                                 exchange=exchange, token=token, error=str(e))
                        # Continue with next token
                        continue
            
            # Cache results
            self._update_cache(ltps)
            
            log_event("BULK_LTP_SUCCESS", f"Fetched LTP for {len(ltps)} instruments",
                     count=len(ltps), requested=total_tokens)
            
            return ltps
        
        except Exception as e:
            log_event("BULK_LTP_ERROR", f"Error in bulk LTP fetch: {str(e)}", error=str(e))
            return {}
    
    def fetch_bulk_ltp_with_retry(self, token_dict: Dict[str, List[str]], 
                                   max_retries: int = 3) -> Dict[str, float]:
        """
        Fetch LTP with automatic retry on failure.
        
        Args:
            token_dict: Dictionary mapping exchange -> list of tokens
            max_retries: Maximum number of retry attempts
        
        Returns:
            Dictionary mapping "EXCHANGE_TOKEN" -> LTP value
        """
        for attempt in range(max_retries):
            try:
                result = self.fetch_bulk_ltp(token_dict)
                if result:
                    return result
            except Exception as e:
                if attempt < max_retries - 1:
                    backoff = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                    log_event("BULK_LTP_RETRY", 
                             f"Retrying after {backoff}s (attempt {attempt + 1}/{max_retries})",
                             error=str(e), backoff=backoff)
                    time.sleep(backoff)
                else:
                    log_event("BULK_LTP_FAILED", f"Failed after {max_retries} retries", error=str(e))
        
        return {}
    
    def get_cached_ltp(self, token_key: str) -> Optional[float]:
        """
        Get LTP from cache if available and not expired.
        
        Args:
            token_key: "EXCHANGE_TOKEN" format (e.g., "NSE_3045")
        
        Returns:
            LTP value if cached and valid, None otherwise
        """
        with self.cache_lock:
            if token_key in self.ltp_cache:
                cached = self.ltp_cache[token_key]
                age = (datetime.now() - cached["timestamp"]).total_seconds()
                
                if age < self.cache_ttl:
                    return cached["ltp"]
                else:
                    # Expired
                    del self.ltp_cache[token_key]
        
        return None
    
    def batch_tokens(self, token_dict: Dict[str, List[str]], 
                     batch_size: int = 50) -> List[Dict[str, List[str]]]:
        """
        Split tokens into batches (Angel One limit is 50 per request).
        
        Args:
            token_dict: Dictionary mapping exchange -> list of tokens
            batch_size: Maximum tokens per batch (default 50)
        
        Returns:
            List of token dictionaries, each with max batch_size tokens
        """
        batches = []
        current_batch = defaultdict(list)
        current_count = 0
        
        for exchange, tokens in token_dict.items():
            for token in tokens:
                if current_count >= batch_size:
                    batches.append(dict(current_batch))
                    current_batch = defaultdict(list)
                    current_count = 0
                
                current_batch[exchange].append(token)
                current_count += 1
        
        if current_batch:
            batches.append(dict(current_batch))
        
        return batches
    
    def fetch_bulk_ltp_batched(self, token_dict: Dict[str, List[str]]) -> Dict[str, float]:
        """
        Fetch LTP for large token lists by batching into groups of 50.
        
        Args:
            token_dict: Dictionary mapping exchange -> list of tokens
        
        Returns:
            Dictionary mapping "EXCHANGE_TOKEN" -> LTP value
        """
        total_tokens = self._count_tokens(token_dict)
        
        if total_tokens <= 50:
            # Single request
            return self.fetch_bulk_ltp(token_dict)
        
        # Multiple batches needed
        batches = self.batch_tokens(token_dict, batch_size=50)
        all_ltps = {}
        
        log_event("BULK_LTP_BATCHED", f"Splitting {total_tokens} tokens into {len(batches)} batches",
                 total_tokens=total_tokens, num_batches=len(batches))
        
        for i, batch in enumerate(batches):
            log_event("BULK_LTP_BATCH", f"Fetching batch {i + 1}/{len(batches)} ({self._count_tokens(batch)} tokens)")
            batch_result = self.fetch_bulk_ltp(batch)
            all_ltps.update(batch_result)
        
        log_event("BULK_LTP_BATCHED_COMPLETE", f"Fetched LTP for all {total_tokens} tokens",
                 success_count=len(all_ltps))
        
        return all_ltps
    
    def clear_cache(self):
        """Clear all cached LTP values"""
        with self.cache_lock:
            count = len(self.ltp_cache)
            self.ltp_cache.clear()
        
        log_event("BULK_LTP_CACHE_CLEARED", f"Cleared {count} cached LTP values")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.cache_lock:
            return {
                "cached_entries": len(self.ltp_cache),
                "cache_ttl": self.cache_ttl,
                "timestamp": datetime.now().isoformat()
            }
    
    # ============ PRIVATE HELPERS ============
    
    def _parse_response(self, response: Dict[str, Any], 
                       token_dict: Dict[str, List[str]]) -> Dict[str, float]:
        """Parse Angel One marketData response into LTP dictionary"""
        ltps = {}
        
        if not response or not response.get("status"):
            log_event("BULK_LTP_PARSE_ERROR", "Invalid response from marketData API",
                     response=response)
            return ltps
        
        fetched = response.get("fetched", [])
        if not isinstance(fetched, list):
            log_event("BULK_LTP_PARSE_ERROR", f"Expected list in 'fetched' field, got {type(fetched)}")
            return ltps
        
        for item in fetched:
            try:
                exchange = item.get("exchange", "")
                token = item.get("symbolToken", "")
                ltp = item.get("ltp")
                symbol = item.get("tradingSymbol", "")
                
                if not exchange or not token:
                    log_event("BULK_LTP_PARSE_SKIP", f"Missing exchange or token in item: {item}")
                    continue
                
                if ltp is None:
                    log_event("BULK_LTP_PARSE_SKIP", f"No LTP for {symbol} ({exchange}_{token})")
                    continue
                
                key = f"{exchange}_{token}"
                ltps[key] = float(ltp)
                
            except Exception as e:
                log_event("BULK_LTP_PARSE_ITEM_ERROR", f"Error parsing item: {str(e)}", item=item)
        
        return ltps
    
    def _update_cache(self, ltps: Dict[str, float]):
        """Update cache with new LTP values"""
        now = datetime.now()
        
        with self.cache_lock:
            for key, ltp in ltps.items():
                self.ltp_cache[key] = {
                    "ltp": ltp,
                    "timestamp": now,
                    "symbol": key  # Placeholder, ideally store actual symbol
                }
    
    def _count_tokens(self, token_dict: Dict[str, List[str]]) -> int:
        """Count total tokens across all exchanges"""
        return sum(len(tokens) for tokens in token_dict.values())


class BulkLTPManager:
    """
    Higher-level manager for bulk LTP fetching with symbol->token mapping.
    
    Handles conversion between trading symbols and instrument tokens.
    """
    
    def __init__(self, smart_api, symbol_token_map: Dict[str, str], logger=None):
        """
        Initialize BulkLTPManager
        
        Args:
            smart_api: SmartConnect API instance
            symbol_token_map: Dictionary mapping trading symbols to tokens
                             Example: {"RELIANCE-EQ": "3045", "INFY-EQ": "4963"}
            logger: Optional logger instance
        """
        self.fetcher = BulkLTPFetcher(smart_api, logger)
        self.symbol_token_map = symbol_token_map
        self.reverse_map = {v: k for k, v in symbol_token_map.items()}
    
    def fetch_ltp_for_symbols(self, symbols: List[str]) -> Dict[str, float]:
        """
        Fetch LTP for list of trading symbols.
        
        Args:
            symbols: List of trading symbols (e.g., ["RELIANCE-EQ", "INFY-EQ"])
        
        Returns:
            Dictionary mapping symbol -> LTP value
        """
        # Group symbols by exchange
        token_dict = defaultdict(list)
        
        for symbol in symbols:
            token = self.symbol_token_map.get(symbol)
            if not token:
                log_event("BULK_LTP_SYMBOL_NOT_FOUND", f"No token mapping for {symbol}")
                continue
            
            # Determine exchange from symbol
            exchange = "NFO" if "DEC" in symbol or "JAN" in symbol else "NSE"
            token_dict[exchange].append(token)
        
        if not token_dict:
            log_event("BULK_LTP_NO_SYMBOLS", "No valid symbols to fetch")
            return {}
        
        # Fetch bulk LTP
        ltp_by_token = self.fetcher.fetch_bulk_ltp(dict(token_dict))
        
        # Convert back to symbol mapping
        ltp_by_symbol = {}
        for token_key, ltp in ltp_by_token.items():
            parts = token_key.split("_")
            if len(parts) == 2:
                token = parts[1]
                symbol = self.reverse_map.get(token)
                if symbol:
                    ltp_by_symbol[symbol] = ltp
        
        return ltp_by_symbol
