"""
Bulk Order Fetcher for Angel One SmartAPI

Efficiently fetches orderBook every 5 seconds and caches the result to avoid
repeated expensive API calls during order confirmation polling.

This prevents rate limit exhaustion when confirming multiple orders - instead of
calling orderBook on every 1-second poll, we call it once every 5 seconds globally.

Architecture:
- Background thread fetches orderBook every 5 seconds
- Results cached for all concurrent order status checks
- check_order_status() reads from cache instead of calling orderBook directly
- Significantly reduces API call rate during order confirmation phase
"""

import time
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from .bot_logging import log_event


class BulkOrderFetcher:
    """
    Bulk order fetcher using Angel One SmartAPI orderBook endpoint.
    
    Fetches all account orders once every 5 seconds and caches the result.
    Multiple concurrent order status checks read from the cache instead of
    calling the expensive orderBook API repeatedly.
    
    This solves the rate limit exhaustion problem where 30 order confirmations
    (1 sec polling x 30 sec timeout) would burn 30 orderBook API calls.
    """
    
    def __init__(self, smart_api, logger=None, fetch_interval_seconds: int = 5):
        """
        Initialize BulkOrderFetcher
        
        Args:
            smart_api: SmartConnect API instance
            logger: Optional logger instance
            fetch_interval_seconds: How often to fetch orderBook (default 5s)
        """
        self.smart_api = smart_api
        self.logger = logger
        self.fetch_interval = fetch_interval_seconds
        
        # Cache: order_id -> order_data (from orderBook response)
        self.order_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()
        self.last_fetch_time = 0.0
        self.last_fetch_success = False
        
        # Background fetch thread
        self.fetch_thread = None
        self.stop_event = threading.Event()
        self.is_running = False
        
        log_event("BULK_ORDER_FETCHER", "BulkOrderFetcher initialized",
                 fetch_interval=fetch_interval_seconds)
    
    def start(self):
        """Start the background orderBook fetcher thread"""
        if self.is_running:
            return
        
        self.stop_event.clear()
        self.is_running = True
        self.fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self.fetch_thread.start()
        log_event("BULK_ORDER_FETCHER", "Background orderBook fetcher started")
    
    def stop(self):
        """Stop the background fetcher thread"""
        if not self.is_running:
            return
        
        self.stop_event.set()
        self.is_running = False
        if self.fetch_thread:
            self.fetch_thread.join(timeout=2.0)
        log_event("BULK_ORDER_FETCHER", "Background orderBook fetcher stopped")
    
    def _fetch_loop(self):
        """Background thread loop that fetches orderBook every N seconds"""
        while not self.stop_event.is_set():
            try:
                current_time = time.time()
                
                # Fetch orderBook at regular intervals
                if current_time - self.last_fetch_time >= self.fetch_interval:
                    self._fetch_orderbook()
                    self.last_fetch_time = current_time
                
                # Sleep briefly to avoid spinning
                time.sleep(0.5)
                
            except Exception as e:
                log_event("BULK_ORDER_FETCHER_ERROR", f"Error in fetch loop: {str(e)}")
                time.sleep(1.0)
    
    def _fetch_orderbook(self):
        """Fetch orderBook from broker and update cache"""
        try:
            if not self.smart_api:
                return
            
            # Call orderBook API (expensive but only once every 5 seconds)
            order_history = self.smart_api.orderBook()
            
            if order_history and order_history.get('status'):
                orders = order_history.get('data', [])
                
                # Update cache with lock protection
                with self.cache_lock:
                    self.order_cache.clear()
                    for order_data in orders:
                        order_id = order_data.get('orderid')
                        if order_id:
                            self.order_cache[order_id] = order_data
                
                self.last_fetch_success = True
                log_event("BULK_ORDER_FETCHER", f"Fetched {len(orders)} orders from broker")
            else:
                log_event("BULK_ORDER_FETCHER_EMPTY", "orderBook returned empty/invalid response")
                
        except Exception as e:
            log_event("BULK_ORDER_FETCHER_API_ERROR", f"Error fetching orderBook: {str(e)}")
            self.last_fetch_success = False
    
    def get_order_data(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached order data without making API call
        
        Args:
            order_id: Order ID to look up
            
        Returns:
            Order data dict from cache, or None if not found
        """
        with self.cache_lock:
            return self.order_cache.get(order_id)
    
    def get_all_orders(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all cached orders
        
        Returns:
            Dictionary of all cached orders by order_id
        """
        with self.cache_lock:
            return dict(self.order_cache)
    
    def is_cache_fresh(self) -> bool:
        """Check if cache was updated recently (within last interval)"""
        if not self.last_fetch_success:
            return False
        age = time.time() - self.last_fetch_time
        return age < (self.fetch_interval + 1)  # Allow 1 second grace
