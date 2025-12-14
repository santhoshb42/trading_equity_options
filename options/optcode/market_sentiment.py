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
        Fetch Put-Call Ratio for all symbols
        
        Returns:
            {symbol: pcr_value, ...}
        """
        try:
            response = self.broker.call_smartapi(
                endpoint='/marketData/v1/putCallRatio',
                method='GET'
            )
            
            if not response or not response.get('status'):
                logger.error(f"PCR_FETCH: API_FAILED | {response.get('message')}")
                return {}
            
            pcr_map = {}
            for item in response.get('data', []):
                trading_symbol = item.get('tradingSymbol', '')
                pcr = float(item.get('pcr', 0))
                
                # Extract base symbol (remove FUT and date)
                base_symbol = trading_symbol.split('FUT')[0] if 'FUT' in trading_symbol else trading_symbol
                pcr_map[base_symbol] = pcr
            
            logger.info(f"PCR_FETCH: SUCCESS | symbols={len(pcr_map)}")
            return pcr_map
            
        except Exception as e:
            logger.error(f"PCR_FETCH: ERROR | {str(e)}")
            return {}
    
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
                logger.error(f"OI_BUILDUP_FETCH: API_FAILED | type={buildup_type} | {response.get('message')}")
                return {}
            
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
            
        except Exception as e:
            logger.error(f"OI_BUILDUP_FETCH: ERROR | type={buildup_type} | {str(e)}")
            return {}
    
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
        from .optconfig import SentimentConfig
        
        # Fetch if not provided
        if pcr is None:
            pcr_map = self.fetch_pcr_ratio()
            pcr = pcr_map.get(symbol)
            if pcr is None:
                return False, "PCR data not available"
        
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
        
        Exit Triggers (STRICT):
        - PCR > EXIT_PCR_BEARISH: Sentiment has turned very bearish, exit CEs
        - OI Buildup fading: oi_change < EXIT_OI_THRESHOLD (conviction weakening)
        - Short Covering active: Indicates weakness in the underlying
        
        Returns:
            (should_exit, reason)
        """
        from .optconfig import SentimentConfig
        
        exit_reasons = []
        
        # Fetch if not provided
        if pcr is None:
            pcr_map = self.fetch_pcr_ratio()
            pcr = pcr_map.get(symbol)
        
        # PCR exit check (strict)
        if pcr is not None:
            if pcr > SentimentConfig.EXIT_PCR_BEARISH:
                exit_reasons.append(f"PCR {pcr:.2f} > {SentimentConfig.EXIT_PCR_BEARISH} (bearish)")
            elif pcr < SentimentConfig.EXIT_PCR_BULLISH:
                exit_reasons.append(f"PCR {pcr:.2f} < {SentimentConfig.EXIT_PCR_BULLISH} (bullish for PE)")
        
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
