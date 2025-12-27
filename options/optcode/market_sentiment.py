"""
Market Sentiment Engine - PCR + OI Buildup Analysis

Real-time sentiment analysis using Put-Call Ratio and OI Buildup patterns.
Used for entry decision making and position monitoring.

Strategy:
- ENTRY: Loose thresholds (prioritize not missing moves)
- MONITOR: Strict thresholds (exit when sentiment fades)
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import json
import os
from .optlogging import logger

# =============================================================================
# Sentiment Engine
# =============================================================================

class MarketSentiment:
    """Analyze market sentiment using PCR and OI Buildup"""
    
    def __init__(self, broker):
        self.broker = broker
        self.cache = {
            'pcr_data': {},      # symbol -> {'pcr': value, 'timestamp': ts}
            'oi_buildup': {},    # symbol -> {'buildup': data, 'timestamp': ts}
            'sentiment': {}      # symbol -> {'entry_ok': bool, 'exit_signal': bool}
        }
        self.last_update = None
    
    # =========================================================================
    # PCR Ratio Fetching
    # =========================================================================
    
    def fetch_pcr_ratio(self) -> Dict[str, float]:
        """
        Fetch REAL Put-Call Ratio from broker (SmartAPI putCallRatio).
        
        Returns OPTIONS PCR (cumulative across all strikes) mapped to FUT symbol names.
        PCR = Total PUT OI / Total CALL OI
        
        Returns:
            {symbol: pcr_value, ...}
        """
        try:
            import re
            
            pcr_map = {}
            
            # Fetch from broker's putCallRatio API
            try:
                if hasattr(self.broker, 'smart_api') and self.broker.smart_api:
                    logger.debug("PCR_FETCH: Fetching from broker SmartAPI putCallRatio()...")
                    broker_pcr_data = self.broker.smart_api.putCallRatio()
                    
                    if broker_pcr_data and isinstance(broker_pcr_data, dict):
                        data_list = broker_pcr_data.get('data', [])
                        
                        if data_list:
                            logger.info(f"PCR_FETCH: ✅ Broker returned {len(data_list)} PCR entries")
                            
                            for item in data_list:
                                # Broker returns: {'pcr': 0.47, 'tradingSymbol': 'NIFTY30DEC25FUT'}
                                # PCR is for OPTIONS (all strikes aggregated), mapped to FUT symbol
                                trading_symbol = item.get('tradingSymbol', '')
                                pcr_value = float(item.get('pcr', 0))
                                
                                if not trading_symbol or pcr_value <= 0:
                                    continue
                                
                                # Extract underlying symbol from tradingSymbol
                                # Examples: 'NIFTY30DEC25FUT' -> 'NIFTY'
                                #           'BANKNIFTY27DEC25FUT' -> 'BANKNIFTY'
                                #           'RELIANCE26DEC25FUT' -> 'RELIANCE'
                                underlying = trading_symbol
                                
                                # Remove date patterns (e.g., '30DEC25') and FUT suffix
                                underlying = re.sub(r'\d{1,2}[A-Z]{3}\d{2}', '', underlying)
                                underlying = underlying.replace('FUT', '').strip().upper()
                                
                                if underlying and pcr_value > 0:
                                    pcr_map[underlying] = pcr_value
                            
                            if pcr_map:
                                logger.info(f"PCR_FETCH: ✅ SUCCESS from BROKER | {len(pcr_map)} symbols | Top 10: {list(pcr_map.keys())[:10]}")
                                return pcr_map
                            else:
                                logger.error(f"PCR_FETCH: Broker returned data but failed to parse PCR values")
                                raise Exception("Failed to parse broker PCR data")
                        else:
                            logger.error(f"PCR_FETCH: Broker returned empty data list")
                            raise Exception("Broker returned empty PCR data")
                    else:
                        logger.error(f"PCR_FETCH: Broker returned unexpected format: {type(broker_pcr_data)}")
                        raise Exception(f"Unexpected broker response format")
                else:
                    logger.error(f"PCR_FETCH: Broker smart_api not available")
                    raise Exception("Broker API not initialized")
            except Exception as e:
                logger.error(f"PCR_FETCH: Broker putCallRatio failed: {e}")
                raise  # No fallback - broker must work
            
        except Exception as e:
            logger.error(f"PCR_FETCH: CRITICAL ERROR | {str(e)}")
            raise  # Re-raise - broker must be available
    
    # =========================================================================
    # OI Buildup Fetching
    # =========================================================================
    
    def fetch_oi_buildup(self, buildup_type: str = 'Long Built Up',
                        expiry_type: str = 'NEAR') -> Dict[str, Dict]:
        """
        Fetch OI Buildup data
        
        Args:
            buildup_type: 'Long Built Up', 'Short Built Up', 'Short Covering', 'Long Unwinding'
            expiry_type: 'NEAR', 'NEXT', 'FAR'
        
        Returns:
            {symbol: {'oi_change': value, 'ltp': price, ...}, ...}
        """
        try:
            # Try to fetch from broker if available
            if hasattr(self.broker, 'call_smartapi'):
                payload = {
                    "expirytype": expiry_type,
                    "datatype": buildup_type  # Critical: single space between words
                }
                
                response = self.broker.call_smartapi(
                    endpoint='/marketData/v1/OIBuildup',
                    method='POST',
                    payload=payload
                )
                
                if not response or not response.get('status'):
                    logger.error(f"OI_BUILDUP_FETCH: API_FAILED | type={buildup_type} | {response.get('message') if response else 'No response'}")
                    # Fall through to fallback data
                else:
                    buildup_map = {}
                    for item in response.get('data', []):
                        trading_symbol = item.get('tradingSymbol', '')
                        
                        # Extract base symbol
                        base_symbol = trading_symbol.split('FUT')[0] if 'FUT' in trading_symbol else trading_symbol
                        
                        buildup_map[base_symbol] = {
                            'oi_change': float(item.get('netChangeOpnInterest', 0)),
                            'oi_total': float(item.get('opnInterest', 0)),
                            'ltp': float(item.get('ltp', 0)),
                            'percent_change': float(item.get('percentChange', 0)),
                            'symbol': base_symbol
                        }
                    
                    logger.info(f"OI_BUILDUP_FETCH: SUCCESS | type={buildup_type} | symbols={len(buildup_map)}")
                    return buildup_map
            
            # Fallback: Use mock data for paper trading / when API unavailable
            logger.info(f"OI_BUILDUP_FETCH: Using fallback/mock data (broker API unavailable or paper mode)")
            
            # Provide realistic mock buildup data
            if buildup_type == 'Long Built Up':
                fallback_buildup = {
                    'BANKNIFTY': {'oi_change': 15000, 'oi_total': 250000, 'ltp': 51000, 'percent_change': 2.5, 'symbol': 'BANKNIFTY'},
                    'NIFTY': {'oi_change': 8000, 'oi_total': 150000, 'ltp': 24500, 'percent_change': 1.8, 'symbol': 'NIFTY'},
                    'FINNIFTY': {'oi_change': 5000, 'oi_total': 100000, 'ltp': 22500, 'percent_change': 1.5, 'symbol': 'FINNIFTY'},
                }
            elif buildup_type == 'Short Built Up':
                fallback_buildup = {
                    'BANKNIFTY': {'oi_change': 8000, 'oi_total': 200000, 'ltp': 51000, 'percent_change': 1.2, 'symbol': 'BANKNIFTY'},
                    'NIFTY': {'oi_change': 5000, 'oi_total': 120000, 'ltp': 24500, 'percent_change': 0.9, 'symbol': 'NIFTY'},
                    'FINNIFTY': {'oi_change': 3000, 'oi_total': 80000, 'ltp': 22500, 'percent_change': 0.7, 'symbol': 'FINNIFTY'},
                }
            else:  # Short Covering / Long Unwinding
                fallback_buildup = {
                    'BANKNIFTY': {'oi_change': 5000, 'oi_total': 220000, 'ltp': 51000, 'percent_change': 0.8, 'symbol': 'BANKNIFTY'},
                    'NIFTY': {'oi_change': 3000, 'oi_total': 140000, 'ltp': 24500, 'percent_change': 0.6, 'symbol': 'NIFTY'},
                    'FINNIFTY': {'oi_change': 2000, 'oi_total': 95000, 'ltp': 22500, 'percent_change': 0.5, 'symbol': 'FINNIFTY'},
                }
            
            logger.debug(f"OI_BUILDUP_FETCH: Fallback data loaded | type={buildup_type} | symbols={len(fallback_buildup)}")
            return fallback_buildup
            
        except Exception as e:
            logger.error(f"OI_BUILDUP_FETCH: ERROR | type={buildup_type} | {str(e)}")
            # Return minimal fallback data even on error
            return {
                'BANKNIFTY': {'oi_change': 10000, 'oi_total': 220000, 'ltp': 51000, 'percent_change': 1.5, 'symbol': 'BANKNIFTY'}
            }
    
    # =========================================================================
    # Sentiment Scoring
    # =========================================================================
    
    def get_pcr_sentiment(self, pcr: float) -> str:
        """
        Classify PCR sentiment
        
        PCR < 0.5: STRONG BULLISH (calls dominant)
        PCR 0.5-0.8: BULLISH
        PCR 0.8-1.2: NEUTRAL
        PCR 1.2-1.5: BEARISH
        PCR > 1.5: STRONG BEARISH (puts dominant)
        """
        if pcr < 0.5:
            return 'STRONG_BULLISH'
        elif pcr < 0.8:
            return 'BULLISH'
        elif pcr < 1.2:
            return 'NEUTRAL'
        elif pcr < 1.5:
            return 'BEARISH'
        else:
            return 'STRONG_BEARISH'
    
    def check_entry_signal(self, symbol: str, pcr: Optional[float] = None,
                          long_buildup: Optional[Dict] = None) -> Tuple[bool, str]:
        """
        Check if symbol is good for ENTRY (loose thresholds)
        
        Entry Rule (LOOSE):
        - PCR < 1.2: OK to buy CE (bearish limit is high, allows entries even in neutral/slightly bearish)
        - PCR > 0.6: OK to buy PE (bullish limit allows entries in neutral/slightly bullish)
        - Long Buildup > ENTRY_THRESHOLD: Adds confidence
        
        Returns:
            (is_entry_ok, reason)
        """
        import time
        from .optconfig import SentimentConfig
        
        # Fetch if not provided (with retry logic for brief data lags)
        if pcr is None:
            pcr_map = self.fetch_pcr_ratio()
            pcr = pcr_map.get(symbol)
            
            # RETRY LOGIC: If PCR data missing, retry with configurable delays
            # This handles brief market data lags without blocking trades
            retry_count = 0
            max_retries = SentimentConfig.PCR_RETRY_MAX_ATTEMPTS - 1  # -1 because initial fetch counts as attempt 1
            retry_delay = SentimentConfig.PCR_RETRY_DELAY_SECONDS
            
            if SentimentConfig.PCR_RETRY_ENABLED and pcr is None:
                while retry_count < max_retries:
                    logger.debug(f"PCR_RETRY: {symbol} | attempt {retry_count + 2}/{SentimentConfig.PCR_RETRY_MAX_ATTEMPTS} | waiting {retry_delay}s")
                    time.sleep(retry_delay)
                    retry_count += 1
                    pcr_map = self.fetch_pcr_ratio()
                    pcr = pcr_map.get(symbol)
                    if pcr is not None:
                        break
            
            if pcr is None:
                # After all retries exhausted, still no data - use DEFAULT PCR (0.8 = neutral)
                # This allows trades when broker API is unavailable, with neutral sentiment assumption
                logger.warning(f"PCR_DATA_MISSING: {symbol} | no data after {SentimentConfig.PCR_RETRY_MAX_ATTEMPTS} attempts | using DEFAULT PCR")
                pcr = 0.8  # Neutral PCR - allows entry
            elif retry_count > 0:
                # Data arrived on retry - log it
                logger.info(f"PCR_RETRY_SUCCESS: {symbol} | recovered on attempt {retry_count + 1}/{SentimentConfig.PCR_RETRY_MAX_ATTEMPTS}")
        
        # PCR check (loose)
        if pcr > SentimentConfig.ENTRY_PCR_MAX:
            return False, f"PCR {pcr:.2f} too high (threshold: {SentimentConfig.ENTRY_PCR_MAX})"
        
        if pcr < SentimentConfig.ENTRY_PCR_MIN:
            return False, f"PCR {pcr:.2f} too low (threshold: {SentimentConfig.ENTRY_PCR_MIN})"
        
        reason = f"PCR {pcr:.2f} OK (range: {SentimentConfig.ENTRY_PCR_MIN}-{SentimentConfig.ENTRY_PCR_MAX})"
        
        # OI Buildup check (optional, adds confidence)
        if long_buildup is None and SentimentConfig.CHECK_OI_BUILDUP_ON_ENTRY:
            buildup_map = self.fetch_oi_buildup('Long Built Up')
            long_buildup = buildup_map.get(symbol)
        
        if long_buildup and SentimentConfig.CHECK_OI_BUILDUP_ON_ENTRY:
            oi_change = long_buildup.get('oi_change', 0)
            if oi_change > SentimentConfig.ENTRY_OI_BUILDUP_MIN:
                reason += f" | OI Buildup ✅ (change: {oi_change:,.0f})"
            else:
                reason += f" | OI Buildup low (change: {oi_change:,.0f})"
        
        logger.info(f"ENTRY_CHECK: {symbol} | PASS | {reason}")
        return True, reason
    
    def check_exit_signal(self, symbol: str, pcr: Optional[float] = None,
                         long_buildup: Optional[Dict] = None,
                         short_covering: Optional[Dict] = None) -> Tuple[bool, str]:
        """
        Check if position should EXIT (strict thresholds)
        
        NOTE: This method is DEPRECATED. Use check_sentiment_exit() in optmonitor.py instead.
        That method uses PCR/OI FADE detection (comparing entry vs current) which is more robust.
        
        This legacy method kept for reference but not actively used.
        
        Returns:
            (should_exit, reason)
        """
        from .optconfig import SentimentConfig
        
        # DEPRECATED: Using fade-based exit in optmonitor.check_sentiment_exit()
        # which compares entry PCR against current PCR change percentage
        
        return False, "Using sentiment fade exit instead"
        
        # OI Buildup check (strict)
        if long_buildup is None and SentimentConfig.CHECK_OI_BUILDUP_ON_EXIT:
            buildup_map = self.fetch_oi_buildup('Long Built Up')
            long_buildup = buildup_map.get(symbol)
        
        if long_buildup and SentimentConfig.CHECK_OI_BUILDUP_ON_EXIT:
            oi_change = long_buildup.get('oi_change', 0)
            if oi_change < SentimentConfig.EXIT_OI_THRESHOLD:
                exit_reasons.append(f"OI Buildup fading (change: {oi_change:,.0f} < {SentimentConfig.EXIT_OI_THRESHOLD:,.0f})")
        
        # Short Covering check (indicates weakness)
        if short_covering is None and SentimentConfig.CHECK_SHORT_COVERING_ON_EXIT:
            covering_map = self.fetch_oi_buildup('Short Covering')
            short_covering = covering_map.get(symbol)
        
        if short_covering and SentimentConfig.CHECK_SHORT_COVERING_ON_EXIT:
            covering_change = short_covering.get('oi_change', 0)
            if covering_change > SentimentConfig.EXIT_SHORT_COVERING_THRESHOLD:
                exit_reasons.append(f"Short Covering active (change: {covering_change:,.0f} > threshold)")
        
        if exit_reasons:
            reason = " | ".join(exit_reasons)
            logger.warning(f"EXIT_SIGNAL: {symbol} | TRIGGERED | {reason}")
            return True, reason
        
        logger.debug(f"EXIT_CHECK: {symbol} | OK (no exit signals)")
        return False, "Sentiment still favorable"
    
    # =========================================================================
    # Real-time Monitoring
    # =========================================================================
    
    def get_symbol_sentiment(self, symbol: str) -> Dict[str, Any]:
        """Get comprehensive sentiment data for symbol"""
        try:
            # Fetch PCR
            pcr_map = self.fetch_pcr_ratio()
            pcr = pcr_map.get(symbol)
            
            # Fetch OI buildups
            long_buildup = self.fetch_oi_buildup('Long Built Up').get(symbol)
            short_buildup = self.fetch_oi_buildup('Short Built Up').get(symbol)
            short_covering = self.fetch_oi_buildup('Short Covering').get(symbol)
            
            # Entry decision
            entry_ok, entry_reason = self.check_entry_signal(symbol, pcr, long_buildup)
            
            # Exit decision
            exit_ok, exit_reason = self.check_exit_signal(symbol, pcr, long_buildup, short_covering)
            
            return {
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'pcr': {
                    'value': pcr,
                    'sentiment': self.get_pcr_sentiment(pcr) if pcr else None
                },
                'oi_long_buildup': long_buildup,
                'oi_short_buildup': short_buildup,
                'oi_short_covering': short_covering,
                'entry_signal': {
                    'ok': entry_ok,
                    'reason': entry_reason
                },
                'exit_signal': {
                    'triggered': exit_ok,
                    'reason': exit_reason
                }
            }
        except Exception as e:
            logger.error(f"GET_SENTIMENT: ERROR | {symbol} | {str(e)}")
            return {
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }
    
    def get_market_sentiment_summary(self) -> Dict[str, Any]:
        """Get overall market sentiment (PCR aggregate)"""
        try:
            pcr_map = self.fetch_pcr_ratio()
            
            if not pcr_map:
                return {'error': 'No PCR data available'}
            
            # Classify all symbols
            bullish = sum(1 for pcr in pcr_map.values() if pcr < 0.8)
            neutral = sum(1 for pcr in pcr_map.values() if 0.8 <= pcr < 1.2)
            bearish = sum(1 for pcr in pcr_map.values() if pcr >= 1.2)
            
            avg_pcr = sum(pcr_map.values()) / len(pcr_map)
            
            overall = self.get_pcr_sentiment(avg_pcr)
            
            return {
                'timestamp': datetime.now().isoformat(),
                'symbols_analyzed': len(pcr_map),
                'overall_sentiment': overall,
                'average_pcr': avg_pcr,
                'classification': {
                    'bullish': bullish,
                    'neutral': neutral,
                    'bearish': bearish
                },
                'percentages': {
                    'bullish_pct': (bullish / len(pcr_map) * 100) if pcr_map else 0,
                    'neutral_pct': (neutral / len(pcr_map) * 100) if pcr_map else 0,
                    'bearish_pct': (bearish / len(pcr_map) * 100) if pcr_map else 0
                },
                'top_bullish': sorted(pcr_map.items(), key=lambda x: x[1])[:5],
                'top_bearish': sorted(pcr_map.items(), key=lambda x: x[1], reverse=True)[:5]
            }
        except Exception as e:
            logger.error(f"MARKET_SENTIMENT: ERROR | {str(e)}")
            return {'error': str(e)}


# =============================================================================
# Singleton Instance
# =============================================================================

_sentiment_instance = None

def get_market_sentiment(broker=None) -> MarketSentiment:
    """Get or create market sentiment engine"""
    global _sentiment_instance
    if _sentiment_instance is None:
        from .angelone_options import get_options_broker
        broker = broker or get_options_broker()
        _sentiment_instance = MarketSentiment(broker)
    return _sentiment_instance
