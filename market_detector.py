#!/usr/bin/env python3
"""
Market Detector Module
Intelligently identifies if alert is for India market (NSE/F&O) or USA market (stocks/options)

Detects market based on:
1. Explicit market field in alert
2. Symbol suffix patterns (e.g., -EQ for India, .US for USA)
3. Known symbol universes
4. Options contract patterns
5. Index detection
"""

from typing import Tuple, Dict, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class Market(Enum):
    """Market enumeration"""
    INDIA = "india"
    USA = "usa"
    UNKNOWN = "unknown"


class MarketDetector:
    """Intelligently detects market from alert"""
    
    # =========================================================================
    # INDIA MARKET SYMBOLS
    # =========================================================================
    INDIA_SYMBOLS = {
        # Equities (NSE) - Top 50+ stocks
        "SBIN", "RELIANCE", "TCS", "INFY", "WIPRO", "AXISBANK", "ICICIBANK",
        "HDFC", "HDFCBANK", "BAJAJFINSV", "KOTAKBANK", "MARUTI", "LT", "ASIANPAINT",
        "SUNPHARMA", "DRREDDY", "CIPLA", "BAJAJFINANCE", "M&M", "BHARTIARTL",
        "HCLTECH", "NTPC", "COALINDIA", "ONGC", "JSWSTEEL", "TATASTEEL",
        "ITC", "NESTLEIND", "BRITANNIA", "MARICO", "GODREJCP", "HINDALCO",
        "HEROMOTOCO", "BOSCHLTD", "AUBANK", "DMART", "GRASIM", "ULTRACEMCO",
        "ADANIPORTS", "ADANIGREEN", "ADANIENT", "POWERGRID", "JSWENERGY",
        
        # Options (NSE F&O) - Indices
        "BANKNIFTY", "NIFTY", "FINNIFTY",
        
        # Futures - Indices
        "NIFTYNXT50", "MIDCPNIFTY"
    }
    
    # =========================================================================
    # USA MARKET SYMBOLS
    # =========================================================================
    USA_SYMBOLS = {
        # Tech stocks - FAANG+ and others
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "NFLX",
        "ADBE", "AVGO", "CSCO", "INTC", "AMD", "QCOM", "AMAT", "CRM", "SNOW",
        
        # Finance - Banks, brokers, insurance
        "JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "TD", "BK",
        "AXP", "BX", "KKR", "APO", "COIN",
        
        # Healthcare - Pharma, medical, biotech
        "JNJ", "UNH", "PFE", "ABBV", "LLY", "MRK", "TMO", "VRTX", "BIIB",
        "GILD", "AMGN", "REGN", "CRSP",
        
        # Energy
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "HAL", "OKE",
        
        # Industrials
        "BA", "CAT", "GE", "HON", "MMM", "RTX", "LMT", "NOC", "HII",
        
        # Indices & ETFs
        "SPX", "SPY", "IVV", "ES", "QQQ", "QQ", "IWM", "DIA", "VTI",
        "XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLRE", "XLU", "XLRE",
        "EEM", "FXI", "EWJ", "EWG", "EWU"
    }
    
    # =========================================================================
    # SUFFIX PATTERNS
    # =========================================================================
    INDIA_SUFFIXES = {"-EQ", "-NFO", "-FUT", "-OPT", ".NSE"}
    USA_SUFFIXES = {".US", ".NYSE", ".NASDAQ"}
    
    @staticmethod
    def detect(alert: Dict[str, Any]) -> Tuple[Market, Dict[str, Any]]:
        """
        Detect market from alert with multi-rule scoring system
        
        Returns:
            (Market enum, confidence dict with scores and reasoning)
        """
        symbol = str(alert.get('symbol', '')).upper().strip()
        confidence = {
            'india_score': 0,
            'usa_score': 0,
            'confidence': 0.0,
            'reasoning': [],
            'detected_market': None
        }
        
        if not symbol:
            return Market.UNKNOWN, confidence
        
        # =====================================================================
        # RULE 1: Explicit market field (HIGHEST PRIORITY)
        # =====================================================================
        market_field = alert.get('market') or alert.get('Market') or alert.get('MARKET')
        if market_field:
            market_field = str(market_field).lower().strip()
            if market_field in ['india', 'nse', 'nsfo', 'nsefo']:
                confidence['india_score'] = 100
                confidence['confidence'] = 1.0
                confidence['reasoning'].append(f"🎯 Explicit market field: '{market_field}' → INDIA")
                confidence['detected_market'] = 'EXPLICIT_FIELD'
                return Market.INDIA, confidence
            elif market_field in ['usa', 'us', 'nasdaq', 'nyse', 'american']:
                confidence['usa_score'] = 100
                confidence['confidence'] = 1.0
                confidence['reasoning'].append(f"🎯 Explicit market field: '{market_field}' → USA")
                confidence['detected_market'] = 'EXPLICIT_FIELD'
                return Market.USA, confidence
        
        # =====================================================================
        # RULE 2: Symbol suffix patterns (HIGH PRIORITY)
        # =====================================================================
        for suffix in MarketDetector.INDIA_SUFFIXES:
            if symbol.endswith(suffix):
                confidence['india_score'] += 80
                confidence['reasoning'].append(f"📌 India suffix detected: {symbol} ends with '{suffix}'")
                break
        
        for suffix in MarketDetector.USA_SUFFIXES:
            if symbol.endswith(suffix):
                confidence['usa_score'] += 60
                confidence['reasoning'].append(f"📌 USA suffix detected: {symbol} ends with '{suffix}'")
                break
        
        # =====================================================================
        # RULE 3: Known symbol universe (HIGH PRIORITY)
        # =====================================================================
        clean_symbol = symbol
        for suffix in MarketDetector.INDIA_SUFFIXES | MarketDetector.USA_SUFFIXES:
            clean_symbol = clean_symbol.replace(suffix, "")
        clean_symbol = clean_symbol.strip()
        
        if clean_symbol in MarketDetector.INDIA_SYMBOLS:
            confidence['india_score'] += 90
            confidence['reasoning'].append(f"🔍 Symbol '{clean_symbol}' found in India universe")
        
        if clean_symbol in MarketDetector.USA_SYMBOLS:
            confidence['usa_score'] += 90
            confidence['reasoning'].append(f"🔍 Symbol '{clean_symbol}' found in USA universe")
        
        # =====================================================================
        # RULE 4: Index detection (HIGHEST PRIORITY AFTER EXPLICIT)
        # =====================================================================
        if clean_symbol in ["BANKNIFTY", "NIFTY", "FINNIFTY"]:
            confidence['india_score'] += 100
            confidence['reasoning'].append(f"📊 India index detected: {clean_symbol}")
            confidence['detected_market'] = 'INDEX'
            return Market.INDIA, confidence
        
        if clean_symbol in ["SPX", "SPY", "IVV", "ES", "QQQ", "QQ", "IWM", "DIA"]:
            confidence['usa_score'] += 100
            confidence['reasoning'].append(f"📊 USA index detected: {clean_symbol}")
            confidence['detected_market'] = 'INDEX'
            return Market.USA, confidence
        
        # =====================================================================
        # RULE 5: Options contract patterns
        # =====================================================================
        if "CE" in symbol or "PE" in symbol:
            # Indian options: BANKNIFTY25XXX1900CE, NIFTY25MAR1900CE, SBIN25DEC1900CE
            confidence['india_score'] += 85
            confidence['reasoning'].append("🔄 India options pattern detected (CE/PE suffix)")
            confidence['detected_market'] = 'OPTIONS_PATTERN'
        
        # USA options: SPX 4500C, SPX 4500P, QQQ 350C, AAPL 150C
        # Try to detect USA options format
        if any(char.isdigit() for char in symbol):
            parts = symbol.split()
            if len(parts) == 2:
                base_symbol = parts[0]
                contract_part = parts[1]
                # Check if it looks like strike price + C/P
                if contract_part.endswith(('C', 'P')):
                    try:
                        strike = float(contract_part[:-1])
                        if base_symbol in MarketDetector.USA_SYMBOLS:
                            confidence['usa_score'] += 85
                            confidence['reasoning'].append(f"🔄 USA options pattern detected ({base_symbol} {strike}C/P)")
                            confidence['detected_market'] = 'OPTIONS_PATTERN'
                    except ValueError:
                        pass
        
        # =====================================================================
        # RULE 6: Contract specification (modern USA options format)
        # =====================================================================
        # SPX 20240621 4500 C or SPX 20240621 4500 CALL
        if 'contract' in str(alert).lower() or 'expiry' in str(alert).lower():
            if 'banknifty' in symbol.lower() or 'nifty' in symbol.lower():
                confidence['india_score'] += 40
                confidence['reasoning'].append("📋 India options contract specification detected")
            elif any(s in symbol.upper() for s in ["SPX", "QQQ", "IWM"]):
                confidence['usa_score'] += 40
                confidence['reasoning'].append("📋 USA options contract specification detected")
        
        # =====================================================================
        # Calculate final result
        # =====================================================================
        max_score = max(confidence['india_score'], confidence['usa_score'])
        
        if max_score == 0:
            # No indicators found - check for patterns
            if "-" in symbol:
                confidence['india_score'] += 20
                confidence['reasoning'].append("⚠️ Contains '-' (common in India symbols)")
            
            max_score = max(confidence['india_score'], confidence['usa_score'])
            if max_score == 0:
                return Market.UNKNOWN, confidence
        
        # Normalize confidence to 0-1 range
        confidence['confidence'] = min(1.0, max_score / 100.0)
        
        if confidence['india_score'] > confidence['usa_score']:
            return Market.INDIA, confidence
        elif confidence['usa_score'] > confidence['india_score']:
            return Market.USA, confidence
        else:
            # Tie - fallback to India (safest default)
            confidence['reasoning'].append("🔄 Tie score - defaulting to INDIA (safest fallback)")
            return Market.INDIA, confidence
    
    @staticmethod
    def is_india(alert: Dict[str, Any]) -> bool:
        """Check if alert is for India market"""
        market, _ = MarketDetector.detect(alert)
        return market == Market.INDIA
    
    @staticmethod
    def is_usa(alert: Dict[str, Any]) -> bool:
        """Check if alert is for USA market"""
        market, _ = MarketDetector.detect(alert)
        return market == Market.USA


# =========================================================================
# Public API
# =========================================================================

# Singleton instance
_detector = MarketDetector()


def detect_market(alert: Dict[str, Any]) -> Tuple[Market, Dict[str, Any]]:
    """
    Public function to detect market from alert
    
    Args:
        alert: Alert dictionary from TradingView
    
    Returns:
        (Market enum, confidence dict with scores and reasoning)
    """
    return _detector.detect(alert)


def is_india_alert(alert: Dict[str, Any]) -> bool:
    """Check if alert is for India"""
    return _detector.is_india(alert)


def is_usa_alert(alert: Dict[str, Any]) -> bool:
    """Check if alert is for USA"""
    return _detector.is_usa(alert)


if __name__ == "__main__":
    # Test the detector
    test_cases = [
        {"symbol": "SBIN-EQ", "action": "BUY"},
        {"symbol": "BANKNIFTY25XXX1900CE", "action": "BUY"},
        {"symbol": "AAPL", "action": "BUY"},
        {"symbol": "SPX 4500C", "action": "BUY"},
        {"symbol": "NIFTY", "action": "BUY"},
        {"symbol": "QQQ", "market": "usa"},
        {"symbol": "XYZ"},  # Unknown
    ]
    
    print("\n" + "="*80)
    print("MARKET DETECTOR TEST RESULTS")
    print("="*80)
    
    for test in test_cases:
        market, conf = detect_market(test)
        print(f"\nAlert: {test}")
        print(f"Detected Market: {market.value.upper()}")
        print(f"Confidence: {conf['confidence']:.0%}")
        for reason in conf['reasoning']:
            print(f"  {reason}")
    
    print("\n" + "="*80)
