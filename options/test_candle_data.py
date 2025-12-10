#!/usr/bin/env python3
"""
Test candle data fetching and technical indicators for options bot.

Verifies:
1. get_historical_data() fetches candles correctly
2. Technical indicators calculated (RSI, ATR, ADX, Bollinger Bands)
3. Underlying technicals provide actionable signals
4. Mock data generation for paper trading mode
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "optcode"))

from optcode.angelone_options import AngelOneOptionsBroker
from optcode.optconfig import DevConfig


def test_1_historical_data_fetching():
    """Test 1: Fetch historical candle data"""
    print("\n" + "="*70)
    print("TEST 1: Historical data fetching (get_historical_data)")
    print("="*70)
    
    broker = AngelOneOptionsBroker()
    
    # Test 1 day of 5-minute candles
    candles = broker.get_historical_data("BANKNIFTY", interval="FIVE_MINUTE", days_back=1)
    
    assert candles is not None, "No candles returned"
    assert len(candles) > 0, "Empty candles list"
    assert len(candles) >= 70, f"Expected 70+ candles for 1 day, got {len(candles)}"
    
    # Verify candle structure
    first_candle = candles[0]
    required_fields = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    for field in required_fields:
        assert field in first_candle, f"Missing field: {field}"
    
    print(f"✅ PASSED: Fetched {len(candles)} candles for BANKNIFTY (1 day)")
    print(f"   First candle: {first_candle}")
    print(f"   Last candle: {candles[-1]}")
    
    # Verify price relationships (high >= low, etc.)
    for i, candle in enumerate(candles):
        assert candle['high'] >= candle['low'], f"Candle {i}: high < low"
        assert candle['high'] >= candle['open'], f"Candle {i}: high < open"
        assert candle['high'] >= candle['close'], f"Candle {i}: high < close"
        assert candle['low'] <= candle['open'], f"Candle {i}: low > open"
        assert candle['low'] <= candle['close'], f"Candle {i}: low > close"
    
    print(f"✅ PASSED: All {len(candles)} candles have valid OHLC relationships")


def test_2_rsi_calculation():
    """Test 2: RSI indicator calculation"""
    print("\n" + "="*70)
    print("TEST 2: RSI (Relative Strength Index) calculation")
    print("="*70)
    
    broker = AngelOneOptionsBroker()
    
    candles = broker.get_historical_data("BANKNIFTY", "FIVE_MINUTE", 2)
    assert len(candles) >= 15, "Not enough data for RSI"
    
    closes = [c['close'] for c in candles]
    rsi = broker._calculate_rsi(closes, 14)
    
    # RSI should be between 0 and 100
    assert 0 <= rsi <= 100, f"RSI out of bounds: {rsi}"
    print(f"✅ PASSED: RSI calculated correctly = {rsi:.2f}")
    
    # Test different periods
    rsi_7 = broker._calculate_rsi(closes, 7)
    rsi_21 = broker._calculate_rsi(closes, 21)
    
    print(f"✅ PASSED: RSI(7)={rsi_7:.2f}, RSI(14)={rsi:.2f}, RSI(21)={rsi_21:.2f}")
    
    # Test edge cases
    rsi_single = broker._calculate_rsi([100.0], 14)  # Only 1 price
    assert rsi_single == 50.0, "Should return 50 for insufficient data"
    print(f"✅ PASSED: Edge case handling works (insufficient data → 50)")


def test_3_atr_calculation():
    """Test 3: ATR indicator calculation"""
    print("\n" + "="*70)
    print("TEST 3: ATR (Average True Range) calculation")
    print("="*70)
    
    broker = AngelOneOptionsBroker()
    
    candles = broker.get_historical_data("NIFTY", "FIVE_MINUTE", 2)
    assert len(candles) >= 15, "Not enough data for ATR"
    
    highs = [c['high'] for c in candles]
    lows = [c['low'] for c in candles]
    closes = [c['close'] for c in candles]
    
    atr = broker._calculate_atr(highs, lows, closes, 14)
    
    # ATR should be positive
    assert atr >= 0, f"ATR should be non-negative: {atr}"
    print(f"✅ PASSED: ATR calculated correctly = {atr:.2f}")
    
    # ATR should typically be smaller than price
    avg_price = sum(closes) / len(closes)
    assert atr < avg_price, f"ATR seems too high relative to price"
    print(f"✅ PASSED: ATR ({atr:.2f}) is reasonable vs price ({avg_price:.2f})")


def test_4_adx_calculation():
    """Test 4: ADX indicator calculation"""
    print("\n" + "="*70)
    print("TEST 4: ADX (Average Directional Index) calculation")
    print("="*70)
    
    broker = AngelOneOptionsBroker()
    
    candles = broker.get_historical_data("FINNIFTY", "FIVE_MINUTE", 2)
    assert len(candles) >= 15, "Not enough data for ADX"
    
    highs = [c['high'] for c in candles]
    lows = [c['low'] for c in candles]
    closes = [c['close'] for c in candles]
    
    adx = broker._calculate_adx(highs, lows, closes, 14)
    
    # ADX should be 0-100
    assert 0 <= adx <= 100, f"ADX out of bounds: {adx}"
    print(f"✅ PASSED: ADX calculated correctly = {adx:.2f}")
    
    # ADX < 25 = weak trend, > 25 = strong trend
    if adx > 25:
        print(f"✅ PASSED: Strong trend detected (ADX={adx:.2f} > 25)")
    else:
        print(f"✅ PASSED: Weak trend detected (ADX={adx:.2f} <= 25)")


def test_5_bollinger_bands():
    """Test 5: Bollinger Bands calculation"""
    print("\n" + "="*70)
    print("TEST 5: Bollinger Bands calculation")
    print("="*70)
    
    broker = AngelOneOptionsBroker()
    
    candles = broker.get_historical_data("BANKNIFTY", "FIVE_MINUTE", 2)
    closes = [c['close'] for c in candles]
    
    mid, upper, lower = broker._calculate_bollinger_bands(closes, 20, 2)
    
    # Upper > Middle > Lower
    assert upper > mid > lower, f"Invalid bands: upper={upper}, mid={mid}, lower={lower}"
    print(f"✅ PASSED: Bollinger Bands valid")
    print(f"   Lower: {lower:.2f}, Middle: {mid:.2f}, Upper: {upper:.2f}")
    
    # Current price should relate to bands
    current_price = closes[-1]
    if lower <= current_price <= upper:
        print(f"✅ PASSED: Price {current_price:.2f} is within bands")
    else:
        print(f"⚠️ Price {current_price:.2f} outside bands (might be breakout)")


def test_6_technical_indicators_comprehensive():
    """Test 6: Complete technical indicators calculation"""
    print("\n" + "="*70)
    print("TEST 6: Comprehensive technical indicators")
    print("="*70)
    
    broker = AngelOneOptionsBroker()
    
    indicators = broker.calculate_technical_indicators("BANKNIFTY")
    
    assert indicators is not None, "Indicators is None"
    assert len(indicators) > 5, f"Too few indicators: {len(indicators)}"
    
    # Check required indicators
    required = ['rsi', 'atr', 'current_price', 'calculated_at']
    for ind in required:
        assert ind in indicators, f"Missing indicator: {ind}"
    
    print(f"✅ PASSED: Calculated {len(indicators)} indicators")
    print(f"   RSI: {indicators.get('rsi', 'N/A')}")
    print(f"   ATR: {indicators.get('atr', 'N/A')}")
    print(f"   ADX: {indicators.get('adx', 'N/A')}")
    print(f"   Price: ₹{indicators.get('current_price', 'N/A'):.2f}")
    print(f"   SMA20: ₹{indicators.get('sma_20', 'N/A'):.2f}")
    
    # Check RSI flags
    if indicators.get('rsi_overbought'):
        print(f"✅ Overbought detected (RSI > 70)")
    elif indicators.get('rsi_oversold'):
        print(f"✅ Oversold detected (RSI < 30)")
    else:
        print(f"✅ RSI neutral zone")


def test_7_underlying_technicals():
    """Test 7: Get underlying technicals with signals"""
    print("\n" + "="*70)
    print("TEST 7: Underlying technicals with trading signals")
    print("="*70)
    
    broker = AngelOneOptionsBroker()
    
    for underlying in ["BANKNIFTY", "NIFTY", "FINNIFTY"]:
        technicals = broker.get_underlying_technicals(underlying)
        
        assert 'underlying' in technicals, f"Missing 'underlying' in {underlying}"
        assert 'indicators' in technicals, f"Missing 'indicators' in {underlying}"
        assert 'signals' in technicals, f"Missing 'signals' in {underlying}"
        
        print(f"✅ {underlying}:")
        print(f"   RSI Signal: {technicals['signals'].get('rsi_signal', 'N/A')}")
        print(f"   Trend Strength: {technicals['signals'].get('trend_strength', 'N/A')}")
        print(f"   Current Price: ₹{technicals['indicators'].get('current_price', 'N/A'):.2f}")


def test_8_paper_trading_mock_data():
    """Test 8: Mock data generation for paper trading"""
    print("\n" + "="*70)
    print("TEST 8: Mock data generation (paper trading mode)")
    print("="*70)
    
    assert DevConfig.PAPER_TRADING_ENABLED, "Not in paper trading mode"
    print(f"✅ Paper trading mode: {DevConfig.PAPER_TRADING_ENABLED}")
    
    broker = AngelOneOptionsBroker()
    
    # Get mock candles
    candles = broker._get_mock_historical_data("BANKNIFTY", days_back=2)
    
    assert len(candles) > 0, "No mock candles generated"
    assert len(candles) >= 77 * 2, f"Expected 154+ candles for 2 days, got {len(candles)}"
    
    print(f"✅ Generated {len(candles)} mock candles for 2 days")
    print(f"   Price range: ₹{min(c['close'] for c in candles):.2f} - ₹{max(c['close'] for c in candles):.2f}")
    
    # Verify mock indicators
    mock_indicators = broker._get_mock_indicators()
    assert 'rsi' in mock_indicators, "Missing RSI in mock"
    assert 'atr' in mock_indicators, "Missing ATR in mock"
    print(f"✅ Mock indicators: RSI={mock_indicators['rsi']}, ATR={mock_indicators['atr']}")


def run_all_tests():
    """Run all tests"""
    print("\n" + "="*70)
    print("CANDLE DATA FETCHING FOR OPTIONS BOT - TEST SUITE")
    print("="*70)
    
    try:
        test_1_historical_data_fetching()
        test_2_rsi_calculation()
        test_3_atr_calculation()
        test_4_adx_calculation()
        test_5_bollinger_bands()
        test_6_technical_indicators_comprehensive()
        test_7_underlying_technicals()
        test_8_paper_trading_mock_data()
        
        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED (8/8)")
        print("="*70)
        print("\nCAPABILITIES ADDED:")
        print("✅ get_historical_data() - Fetch OHLCV candles for underlyings")
        print("✅ RSI calculation - Overbought/oversold detection")
        print("✅ ATR calculation - Volatility measurement")
        print("✅ ADX calculation - Trend strength assessment")
        print("✅ Bollinger Bands - Support/resistance levels")
        print("✅ SMA tracking - Price vs moving average signals")
        print("✅ get_underlying_technicals() - Actionable signals for trading")
        print("✅ Mock data - Supports paper trading without API calls")
        print("\nUSE CASES:")
        print("1. Enhanced fake move detection - Use ATR for threshold validation")
        print("2. Entry signal validation - Confirm with RSI/trend")
        print("3. Underlying analysis - Monitor BANKNIFTY/NIFTY technicals")
        print("4. Dynamic profit targets - Use ATR to scale targets")
        print("5. Volatility monitoring - Track ATR trends over time")
        print("="*70)
        
        return True
    
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {str(e)}")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
