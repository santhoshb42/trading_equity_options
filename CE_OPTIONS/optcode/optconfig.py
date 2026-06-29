"""
Options Trading Bot Configuration Module

All AngelOne parameters, budget settings, and options-specific configuration.
Completely independent from equity bot, shares only webhook alerts from TradingView.
"""

import os
from datetime import datetime, time as dt_time
from typing import Dict, Any, Tuple, Optional
from pathlib import Path

# Optional dotenv support
try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False

# =============================================================================
# Environment and Paths
# =============================================================================

# Bot root directory
BASE_DIR = Path(__file__).parent.parent

# Mode: OTM or ITM — set by main.py via --mode arg before any imports
BOT_MODE = os.getenv("BOT_MODE", "OTM").upper()

# Runtime files live inside CE_OPTIONS/OTM/ or CE_OPTIONS/ITM/ (never shared)
DATA_DIR = BASE_DIR / BOT_MODE / "data"

# Load shared credentials, then mode-specific overrides (PORT, STRIKE_OFFSET)
ENV_FILE = BASE_DIR / "tools" / ".env"
ENV_MODE_FILE = BASE_DIR / "tools" / f".env.{BOT_MODE.lower()}"
if _DOTENV_AVAILABLE:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)
    if ENV_MODE_FILE.exists():
        load_dotenv(ENV_MODE_FILE, override=True)

# Data files
INSTRUMENT_FILE = BASE_DIR / "tools" / "instrument.json"
SESSION_FILE = DATA_DIR / "session.json"
POSITIONS_FILE = DATA_DIR / "positions.json"
OPTION_CHAIN_CACHE = DATA_DIR / "option_chain_cache.json"

# =============================================================================
# AngelOne API Configuration (Same as Equity)
# =============================================================================

class AngelOneConfig:
    """AngelOne SmartAPI configuration for options trading"""
    
    API_KEY = os.getenv("ANGEL_API_KEY", "")
    CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "")
    PASSWORD = os.getenv("ANGEL_PASSWORD", "")
    TOTP_KEY = os.getenv("ANGEL_TOTP_KEY", "")
    
    # API endpoints
    BASE_URL = "https://smartapi.angelbroking.com"
    
    # Order parameters
    EXCHANGE = "NFO"  # National Futures and Options exchange
    PRODUCT_TYPE = "INTRADAY"  # Intraday for options (options expire same day typically)
    DURATION = "DAY"
    ORDER_TYPE = "MARKET"  # MARKET, LIMIT, STOPLOSS-MARKET, STOPLOSS-LIMIT

# =============================================================================
# Options Capital Configuration
# =============================================================================

class OptionsCapitalConfig:
    """Capital and budget management for options trading"""
    
    # Total available capital for options
    MAX_CAPITAL = float(os.getenv("OPTIONS_MAX_CAPITAL", "900000"))  # ₹9,00,000
    
    # Capital per trade (options contracts, 30K per trade)
    CAP_PER_TRADE = float(os.getenv("OPTIONS_CAP_PER_TRADE", "30000"))  # ₹30,000 per options trade
    
    # Maximum concurrent positions (100 slots for aggressive options trading)
    MAX_SLOTS = int(os.getenv("OPTIONS_MAX_SLOTS", "100"))  # Max 100 concurrent option positions
    
    # Maximum trades per day are tracked separately for index and non-index symbols.
    # Keep OPTIONS_MAX_TRADES_PER_DAY as the legacy non-index knob for backward compatibility.
    MAX_NON_INDEX_TRADES_PER_DAY = int(
        os.getenv("OPTIONS_MAX_NON_INDEX_TRADES_PER_DAY", os.getenv("OPTIONS_MAX_TRADES_PER_DAY", "100"))
    )
    
    # Reserve capital (emergency buffer for options)
    RESERVE_CAPITAL = float(os.getenv("OPTIONS_RESERVE_CAPITAL", "50000"))  # ₹50,000 reserve

    # Daily loss circuit breaker (% of budget_used for the day)
    # e.g. 3.0 → stop new entries once total_pnl < -3% of budget_used
    # Only activates after DAILY_CB_MIN_TRADES have been placed (avoids shutting down
    # on the first 2 HARD_SL hits before enough data exists to judge the day).
    # Set DAILY_LOSS_LIMIT_PCT=0 to disable entirely.
    DAILY_LOSS_LIMIT_PCT = float(os.getenv("OPTIONS_DAILY_LOSS_LIMIT_PCT", "3.0"))
    DAILY_CB_MIN_TRADES  = int(os.getenv("OPTIONS_DAILY_CB_MIN_TRADES", "10"))

    # Market trend-aware position sizing (Pine Script → market_trend field in alert JSON)
    # GOOD    → trade at CAP_PER_TRADE_GOOD
    # NEUTRAL → trade at CAP_PER_TRADE_NEUTRAL
    # BAD     → trade at CAP_PER_TRADE_BAD
    # Currently all are ₹30K (capital-constrained). To scale specific trend days:
    #   set OPTIONS_CAP_PER_TRADE_GOOD=60000 in .env when capital is available.
    CAP_PER_TRADE_GOOD    = float(os.getenv("OPTIONS_CAP_PER_TRADE_GOOD",    "30000"))
    CAP_PER_TRADE_BAD     = float(os.getenv("OPTIONS_CAP_PER_TRADE_BAD",     "30000"))
    CAP_PER_TRADE_NEUTRAL = float(os.getenv("OPTIONS_CAP_PER_TRADE_NEUTRAL", "30000"))
    INDEX_CAP_PER_TRADE_GOOD = float(os.getenv("OPTIONS_INDEXES_CAP_PER_TRADE_GOOD", os.getenv("OPTIONS_NIFTY_CAP_PER_TRADE_GOOD", "0")))
    INDEX_CAP_PER_TRADE_BAD = float(os.getenv("OPTIONS_INDEXES_CAP_PER_TRADE_BAD", os.getenv("OPTIONS_NIFTY_CAP_PER_TRADE_BAD", "0")))
    INDEX_CAP_PER_TRADE_NEUTRAL = float(os.getenv("OPTIONS_INDEXES_CAP_PER_TRADE_NEUTRAL", os.getenv("OPTIONS_NIFTY_CAP_PER_TRADE_NEUTRAL", "0")))
    MAX_INDEX_TRADES_PER_DAY = int(os.getenv("OPTIONS_MAX_INDEX_TRADES_PER_DAY", "10"))
    MAX_TRADES_PER_DAY = MAX_NON_INDEX_TRADES_PER_DAY + MAX_INDEX_TRADES_PER_DAY
    INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "SENSEX"}

    # Liquidity guard: simple flat rules.
    # Max lots = 10% of OI lots (pre-capped in optapi.py before this check).
    # Max volume participation = 20% of daily volume.
    # Spread thresholds still scale by premium (advisory only, not a hard block).
    LIQUIDITY_MIN_PREMIUM = float(os.getenv("OPTIONS_LIQUIDITY_MIN_PREMIUM", "5.0"))
    LIQUIDITY_MAX_PREMIUM = float(os.getenv("OPTIONS_LIQUIDITY_MAX_PREMIUM", "250.0"))
    MAX_OI_PARTICIPATION = float(os.getenv("OPTIONS_MAX_OI_PARTICIPATION", "0.10"))       # 10% of OI lots
    MAX_VOLUME_PARTICIPATION = float(os.getenv("OPTIONS_MAX_VOLUME_PARTICIPATION", "0.20")) # 20% of daily volume
    MAX_SPREAD_PCT_AT_LOW_PREMIUM = float(os.getenv("OPTIONS_MAX_SPREAD_PCT_AT_LOW_PREMIUM", "4.0"))
    MAX_SPREAD_PCT_AT_HIGH_PREMIUM = float(os.getenv("OPTIONS_MAX_SPREAD_PCT_AT_HIGH_PREMIUM", "1.25"))
    MAX_SPREAD_RS_AT_LOW_PREMIUM = float(os.getenv("OPTIONS_MAX_SPREAD_RS_AT_LOW_PREMIUM", "0.20"))
    MAX_SPREAD_RS_AT_HIGH_PREMIUM = float(os.getenv("OPTIONS_MAX_SPREAD_RS_AT_HIGH_PREMIUM", "1.00"))
    HARD_SPREAD_PCT_REJECT = float(os.getenv("OPTIONS_HARD_SPREAD_PCT_REJECT", "5.0"))  # spread > 5% = hard block

    @classmethod
    def get_cap_for_market_trend(cls, market_trend: str) -> float:
        """Return capital-per-trade based on the Pine Script market_trend field.

        Allow trading on all market trends while preserving trend-aware sizing.
        Falls back to CAP_PER_TRADE_NEUTRAL for unknown/missing field.
        """
        t = (market_trend or "").strip().upper()
        if t == "GOOD":
            return cls.CAP_PER_TRADE_GOOD
        if t == "BAD":
            return cls.CAP_PER_TRADE_BAD
        return cls.CAP_PER_TRADE_NEUTRAL

    @classmethod
    def canonicalize_underlying(cls, symbol: str) -> str:
        raw = (symbol or "").strip().upper()
        if ":" in raw:
            raw = raw.split(":", 1)[1]
        compact = raw.replace(" ", "")
        aliases = {
            "NIFTY50": "NIFTY",
            "NIFTY": "NIFTY",
            "BANKNIFTY": "BANKNIFTY",
            "SENSEX": "SENSEX",
        }
        return aliases.get(compact, compact)

    @classmethod
    def is_index_underlying(cls, symbol: str) -> bool:
        return cls.canonicalize_underlying(symbol) in cls.INDEX_UNDERLYINGS

    @classmethod
    def get_cap_for_symbol_and_trend(cls, symbol: str, market_trend: str) -> float:
        if cls.is_index_underlying(symbol):
            t = (market_trend or "").strip().upper()
            if t == "GOOD" and cls.INDEX_CAP_PER_TRADE_GOOD > 0:
                return cls.INDEX_CAP_PER_TRADE_GOOD
            if t == "BAD" and cls.INDEX_CAP_PER_TRADE_BAD > 0:
                return cls.INDEX_CAP_PER_TRADE_BAD
            if cls.INDEX_CAP_PER_TRADE_NEUTRAL > 0:
                return cls.INDEX_CAP_PER_TRADE_NEUTRAL
        return cls.get_cap_for_market_trend(market_trend)

    # Commission and charges per trade
    BROKERAGE_PER_TRADE = 15.0  # ₹15 flat brokerage per options order
    STT_PERCENTAGE = 0.005  # 0.5% STT on sell side (higher for options)
    TRANSACTION_CHARGES = 0.00005  # Higher for options on NSE
    GST_PERCENTAGE = 0.18  # 18% GST on brokerage
    
    @classmethod
    def calculate_total_charges(cls, trade_value: float) -> float:
        """Calculate total charges for an options trade"""
        brokerage = cls.BROKERAGE_PER_TRADE
        stt = trade_value * cls.STT_PERCENTAGE
        trans = trade_value * cls.TRANSACTION_CHARGES
        gst = (brokerage + trans) * cls.GST_PERCENTAGE
        return brokerage + stt + trans + gst
    
    @classmethod
    def calculate_quantity_for_capital(cls, premium: float, capital: float, lot_size: int = 1) -> int:
        """
        Calculate exchange-valid option quantity using whole lots only.

        Premium is quoted per option unit, so one lot costs:
          lot_cost = premium * lot_size

        Example:
          Budget: ₹30,000
          Premium: ₹12.50
          Lot Size: 775
          Lot cost: ₹9,687.50
          Affordable lots: floor(30,000 / 9,687.50) = 3
          Quantity: 3 × 775 = 2,325
        """
        if premium <= 0 or capital <= 0:
            return 0

        effective_lot_size = max(int(lot_size or 1), 1)
        lot_cost = premium * effective_lot_size
        if lot_cost <= 0:
            return 0

        affordable_lots = int(capital / lot_cost)
        if affordable_lots < 1:
            return 0

        quantity = affordable_lots * effective_lot_size
        actual_cost = quantity * premium
        if actual_cost > capital:
            affordable_lots -= 1
            if affordable_lots < 1:
                return 0
            quantity = affordable_lots * effective_lot_size

        return quantity

    @classmethod
    def get_dynamic_liquidity_limits(cls, budget: float, premium: float) -> Dict[str, float]:
        """Simple flat liquidity limits. OI lot cap is pre-applied in optapi.py; this is a backstop."""
        floor_premium = min(cls.LIQUIDITY_MIN_PREMIUM, cls.LIQUIDITY_MAX_PREMIUM)
        ceil_premium = max(cls.LIQUIDITY_MIN_PREMIUM, cls.LIQUIDITY_MAX_PREMIUM)
        effective_premium = min(max(premium, floor_premium), ceil_premium)
        premium_span = max(ceil_premium - floor_premium, 1.0)
        premium_ratio = (effective_premium - floor_premium) / premium_span

        max_spread_pct = (
            cls.MAX_SPREAD_PCT_AT_LOW_PREMIUM +
            (cls.MAX_SPREAD_PCT_AT_HIGH_PREMIUM - cls.MAX_SPREAD_PCT_AT_LOW_PREMIUM) * premium_ratio
        )
        max_spread_rs = (
            cls.MAX_SPREAD_RS_AT_LOW_PREMIUM +
            (cls.MAX_SPREAD_RS_AT_HIGH_PREMIUM - cls.MAX_SPREAD_RS_AT_LOW_PREMIUM) * premium_ratio
        )

        return {
            'effective_budget': budget,
            'budget_ratio': 1.0,
            'effective_premium': effective_premium,
            'premium_ratio': premium_ratio,
            'min_required_oi': 0,
            'max_volume_participation': cls.MAX_VOLUME_PARTICIPATION,
            'max_oi_participation': cls.MAX_OI_PARTICIPATION,
            'max_spread_pct': max(0.25, max_spread_pct),
            'max_spread_rs': max(0.05, max_spread_rs),
        }

    @classmethod
    def evaluate_liquidity_for_order(
        cls,
        *,
        budget: float,
        quantity: int,
        premium: float,
        volume: int,
        open_interest: int,
        bid: float = 0.0,
        ask: float = 0.0,
        bid_ask_spread_pct: Optional[float] = None,
        lot_size: int = 1,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Reject oversized orders when order size is too large for contract liquidity."""
        if quantity <= 0 or premium <= 0:
            return False, "Invalid order sizing for liquidity check", {}

        limits = cls.get_dynamic_liquidity_limits(budget, premium)
        effective_lot_size = max(lot_size, 1)
        order_lots = quantity / effective_lot_size
        order_value = quantity * premium

        metrics = {
            'budget': budget,
            'effective_budget': limits['effective_budget'],
            'effective_premium': limits['effective_premium'],
            'premium_ratio': limits['premium_ratio'],
            'order_value': order_value,
            'quantity': quantity,
            'order_lots': order_lots,
            'volume': float(volume or 0),
            'open_interest': float(open_interest or 0),
            'min_required_oi': limits['min_required_oi'],
            'max_volume_participation': limits['max_volume_participation'],
            'max_oi_participation': limits['max_oi_participation'],
            'bid': float(bid or 0.0),
            'ask': float(ask or 0.0),
            'max_spread_pct': limits['max_spread_pct'],
            'max_spread_rs': limits['max_spread_rs'],
            'volume_data_missing': False,
            'spread_advisory': False,
            'spread_advisory_reason': None,
        }

        if not open_interest or open_interest <= 0:
            return False, "Live OI unavailable for contract - refusing entry", metrics

        if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
            metrics['bid_ask_spread'] = None
            metrics['bid_ask_spread_pct'] = float(bid_ask_spread_pct or 0.0)
            metrics['spread_advisory'] = True
            metrics['spread_advisory_reason'] = "Live bid/ask unavailable - logging only"
        else:
            spread_abs = max(ask - bid, 0.0)
            spread_pct = bid_ask_spread_pct
            if spread_pct is None:
                spread_pct = (spread_abs / premium * 100.0) if premium > 0 else 0.0

            metrics['bid_ask_spread'] = spread_abs
            metrics['bid_ask_spread_pct'] = float(spread_pct)
            entry_bid_gap_pct = ((premium - bid) / premium * 100.0) if premium > 0 else 0.0
            metrics['entry_bid_gap_pct'] = float(entry_bid_gap_pct)

            if spread_pct > cls.HARD_SPREAD_PCT_REJECT:
                return (
                    False,
                    f"Bid/ask spread {spread_pct:.1f}% exceeds hard limit {cls.HARD_SPREAD_PCT_REJECT:.0f}% — too wide to enter safely",
                    metrics,
                )

            if spread_abs > limits['max_spread_rs'] or spread_pct > limits['max_spread_pct']:
                metrics['spread_advisory'] = True
                metrics['spread_advisory_reason'] = (
                    f"Bid/ask spread {spread_pct:.2f}% (₹{spread_abs:.2f}) — advisory"
                )

        if volume and volume > 0:
            metrics['volume_participation'] = quantity / max(volume, 1)
        else:
            metrics['volume_data_missing'] = True

        oi_participation = quantity / max(open_interest, 1)
        metrics['oi_participation'] = oi_participation

        return True, "Liquidity check passed", metrics
    
    @classmethod
    def get_today_live_summary(cls) -> dict:
        """Read live_data.json and return the fields needed for the circuit breaker.
        total_pnl_percent is already computed by live_data_tracker as
        (total_pnl / budget_used * 100) — use it directly instead of re-deriving.
        Fail-CLOSED on any error: returns a sentinel that satisfies trades_today >= min_trades
        so the circuit breaker is conservatively active rather than silently disabled."""
        import json as _json
        live_data_file = DATA_DIR / "live_data.json"
        try:
            if live_data_file.exists():
                with open(live_data_file) as f:
                    payload = _json.load(f)
                s = payload.get('summary')
                if s is None:
                    index_summary = payload.get('index_summary', {})
                    non_index_summary = payload.get('non_index_summary', {})
                    budget_used = float(index_summary.get('budget_used', 0.0)) + float(non_index_summary.get('budget_used', 0.0))
                    total_pnl = float(index_summary.get('total_pnl', 0.0)) + float(non_index_summary.get('total_pnl', 0.0))
                    trades_today = int(index_summary.get('trades_today', 0)) + int(non_index_summary.get('trades_today', 0))
                    total_pnl_percent = (total_pnl / budget_used * 100) if budget_used > 0 else 0.0
                    s = {
                        'total_pnl': total_pnl,
                        'total_pnl_percent': total_pnl_percent,
                        'budget_used': budget_used,
                        'total_trades_today': trades_today,
                    }
                return {
                    'total_pnl':         float(s.get('total_pnl', 0.0)),
                    'total_pnl_percent': float(s.get('total_pnl_percent', 0.0)),
                    'budget_used':       float(s.get('budget_used', 0.0)),
                    'trades_today':      int(s.get('total_trades_today', 0)),
                }
        except Exception:
            pass
        # Fail-closed: large trades_today so the min_trades guard is always satisfied,
        # and pnl=0 so the CB fires only if loss limit is also breached independently.
        # This prevents "file missing → CB fully disabled" silent failure in production.
        return {'total_pnl': 0.0, 'total_pnl_percent': 0.0, 'budget_used': 0.0, 'trades_today': 999}

    @classmethod
    def get_available_capital(cls, used_capital: float) -> float:
        """Get available capital after reserves"""
        available = cls.MAX_CAPITAL - cls.RESERVE_CAPITAL - used_capital
        return max(0, available)

    @classmethod
    def get_trade_limit_for_underlying(cls, underlying: Optional[str] = None) -> int:
        if cls.is_index_underlying(underlying or ''):
            return cls.MAX_INDEX_TRADES_PER_DAY
        return cls.MAX_NON_INDEX_TRADES_PER_DAY

    @classmethod
    def _get_daily_trade_state_file(cls):
        """Return the mode-aware daily trade counter file.

        LIVE trading must not inherit the existing PAPER counter for the day,
        otherwise a live pilot can be blocked by earlier simulated trades.
        """
        from datetime import datetime

        date_str = datetime.now().strftime('%Y-%m-%d')
        if OptionsTradingConfig.TRADING_MODE == "LIVE":
            return DATA_DIR / f"daily_trades_live_{date_str}.json"
        return DATA_DIR / f"daily_trades_{date_str}.json"

    @classmethod
    def _derive_today_index_trade_count(cls) -> int:
        import json

        today = datetime.now().date().isoformat()
        count = 0

        pnl_file = DATA_DIR / "option_pnl_history.json"
        if pnl_file.exists():
            try:
                with open(pnl_file) as f:
                    pnl_data = json.load(f)
                closed_trades = pnl_data if isinstance(pnl_data, list) else pnl_data.get('trades', [])
                count += sum(
                    1
                    for trade in closed_trades
                    if ((trade.get('closed_at', '') or trade.get('exit_time', '')).startswith(today)
                        and cls.is_index_underlying(trade.get('underlying') or trade.get('symbol') or ''))
                )
            except Exception:
                pass

        positions_file = DATA_DIR / "option_positions.json"
        if positions_file.exists():
            try:
                with open(positions_file) as f:
                    pos_data = json.load(f)
                positions_raw = pos_data.get('positions', [])
                open_positions = list(positions_raw.values()) if isinstance(positions_raw, dict) else positions_raw
                count += sum(
                    1
                    for position in open_positions
                    if (str(position.get('entry_time', '')).startswith(today)
                        and cls.is_index_underlying(position.get('underlying') or position.get('symbol') or ''))
                )
            except Exception:
                pass

        return count

    @classmethod
    def _read_daily_trade_state(cls) -> Dict[str, int]:
        import json

        state = {
            'trades_placed': 0,
            'index_trades_placed': 0,
            'non_index_trades_placed': 0,
            'max_allowed': cls.MAX_TRADES_PER_DAY,
            'index_max_allowed': cls.MAX_INDEX_TRADES_PER_DAY,
            'non_index_max_allowed': cls.MAX_NON_INDEX_TRADES_PER_DAY,
        }
        daily_state_file = cls._get_daily_trade_state_file()

        if not daily_state_file.exists():
            return state

        try:
            with open(daily_state_file) as f:
                data = json.load(f)
            if 'index_trades_placed' in data:
                state['index_trades_placed'] = int(data.get('index_trades_placed', 0))
            else:
                state['index_trades_placed'] = cls._derive_today_index_trade_count()
            if 'non_index_trades_placed' in data:
                state['non_index_trades_placed'] = int(data.get('non_index_trades_placed', 0))
            else:
                legacy_total = int(data.get('trades_placed', 0))
                state['non_index_trades_placed'] = max(0, legacy_total - state['index_trades_placed'])
            state['trades_placed'] = state['index_trades_placed'] + state['non_index_trades_placed']
            state['max_allowed'] = int(data.get('max_allowed', cls.MAX_TRADES_PER_DAY))
            state['index_max_allowed'] = int(data.get('index_max_allowed', cls.MAX_INDEX_TRADES_PER_DAY))
            state['non_index_max_allowed'] = int(data.get('non_index_max_allowed', cls.MAX_NON_INDEX_TRADES_PER_DAY))
        except Exception:
            return state

        return state

    @classmethod
    def get_daily_trade_counts(cls) -> Dict[str, int]:
        state = cls._read_daily_trade_state()
        return {
            'total': int(state.get('trades_placed', 0)),
            'index': int(state.get('index_trades_placed', 0)),
            'non_index': int(state.get('non_index_trades_placed', 0)),
            'max_allowed': int(state.get('max_allowed', cls.MAX_TRADES_PER_DAY)),
            'index_max_allowed': int(state.get('index_max_allowed', cls.MAX_INDEX_TRADES_PER_DAY)),
            'non_index_max_allowed': int(state.get('non_index_max_allowed', cls.MAX_NON_INDEX_TRADES_PER_DAY)),
        }
    
    @classmethod
    def get_daily_trade_count(cls) -> int:
        """Get number of trades placed today (reads from daily state file)"""
        return cls.get_daily_trade_counts()['total']

    @classmethod
    def get_daily_index_trade_count(cls) -> int:
        return cls.get_daily_trade_counts()['index']

    @classmethod
    def get_daily_non_index_trade_count(cls) -> int:
        return cls.get_daily_trade_counts()['non_index']
    
    @classmethod
    def increment_daily_trade_count(cls, underlying: Optional[str] = None) -> Dict[str, int]:
        """Increment daily trade counters and return the updated totals."""
        from datetime import datetime
        import json
        import tempfile

        daily_state_file = cls._get_daily_trade_state_file()
        
        try:
            # Create temp directory if needed
            daily_state_file.parent.mkdir(parents=True, exist_ok=True)
            state = cls._read_daily_trade_state()
            index_count = int(state.get('index_trades_placed', 0))
            non_index_count = int(state.get('non_index_trades_placed', 0))
            if cls.is_index_underlying(underlying or ''):
                index_count += 1
            else:
                non_index_count += 1
            total_count = index_count + non_index_count
            
            # Atomic write using temp file
            new_data = {
                'date': datetime.now().isoformat(),
                'trades_placed': total_count,
                'index_trades_placed': index_count,
                'non_index_trades_placed': non_index_count,
                'max_allowed': cls.MAX_TRADES_PER_DAY,
                'index_max_allowed': cls.MAX_INDEX_TRADES_PER_DAY,
                'non_index_max_allowed': cls.MAX_NON_INDEX_TRADES_PER_DAY,
            }
            
            # Write to temp file first, then rename (atomic on Unix)
            with tempfile.NamedTemporaryFile(
                mode='w', 
                dir=daily_state_file.parent, 
                delete=False,
                suffix='.json'
            ) as tmp:
                json.dump(new_data, tmp)
                tmp_path = tmp.name
            
            # Atomic rename
            import os
            os.replace(tmp_path, str(daily_state_file))
            
            return {
                'total': total_count,
                'index': index_count,
                'non_index': non_index_count,
                'max_allowed': cls.MAX_TRADES_PER_DAY,
                'index_max_allowed': cls.MAX_INDEX_TRADES_PER_DAY,
                'non_index_max_allowed': cls.MAX_NON_INDEX_TRADES_PER_DAY,
            }
        except Exception as e:
            from .optlogging import logger
            logger.error(f"DAILY_TRADE_COUNT: ERROR incrementing | {str(e)}")
            return {
                'total': max(1, cls.get_daily_trade_count()),
                'index': cls.get_daily_index_trade_count(),
                'non_index': cls.get_daily_non_index_trade_count(),
                'max_allowed': cls.MAX_TRADES_PER_DAY,
                'index_max_allowed': cls.MAX_INDEX_TRADES_PER_DAY,
                'non_index_max_allowed': cls.MAX_NON_INDEX_TRADES_PER_DAY,
            }

# =============================================================================
# Options Trading Configuration
# =============================================================================

class OptionsTradingConfig:
    """Options-specific trading strategy and risk management"""
    
    # Trading mode comes from environment so CE and PE can run in different modes.
    TRADING_MODE = os.getenv("TRADING_MODE", "PAPER").strip().upper()
    
    # Underlying indexes for options trading (legacy - keep for backward compatibility)
    UNDERLYING_INDEXES = ["BANKNIFTY", "NIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"]  # Preferred NSE underlying indexes
    
    # F&O Universe - Complete NSE stock list for deriving strikes
    FO_UNIVERSE = [
        "PEL", "ABB", "ABCAPITAL", "ACC", "ADANIENSOL", "ADANIENT", 
        "ADANIGREEN", "ADANIPORTS", "ALKEM", "AMBER", "AMBUJACEM", "ANGELONE", "APLAPOLLO", 
        "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ASTRAL", "AUBANK", "AUROPHARMA", 
        "AXISBANK", "BAJAJFINSV", "BAJAJ_AUTO", "BAJAJHLDNG", "BAJFINANCE", "BALKRISIND", "BANDHANBNK", "BANKBARODA", 
        "BDL", "BEL", "BHARATFORG", "BHARTIARTL", "BHEL", "BIOCON", "BLUESTARCO", "BOSCHLTD", 
        "BPCL", "BRITANNIA", "BSE", "BSOFT", "CAMS", "CANBK", "CESC", "CGPOWER", 
        "CHOLAFIN", "CIPLA", "COCHINSHIP", "COALINDIA", "COFORGE", "COLPAL", "CONCOR", "CROMPTON", 
        "CUMMINSIND", "CYIENT", "DABUR", "DALBHARAT", "DELHIVERY", "DIVISLAB", "DMART", "DIXON", "DLF", 
        "DRREDDY", "EICHERMOT", "ETERNAL", "EXIDEIND", "FEDERALBNK", "FORCEMOT", "FORTIS", "GAIL", "GLENMARK", 
        "GMRAIRPORT", "GODFRYPHLP", "GODREJCP", "GODREJPROP", "GRANULES", "GRASIM", "HAL", "HAVELLS", 
        "HCLTECH", "HDFCAMC", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HFCL", "HINDALCO", 
        "HINDCOPPER", "HINDPETRO", "HINDUNILVR", "HINDZINC", "HUDCO", "ICICIBANK", "ICICIGI", 
        "ICICIPRULI", "IDEA", "IDFCFIRSTB", "IEX", "IIFL", "INDHOTEL", "INDIANB", 
        "INDIGO", "INDUSINDBK", "INDUSTOWER", "INFY", "INOXWIND", "IOC", "IRB", "IRCTC", 
        "IREDA", "IRFC", "ITC", "JINDALSTEL", "JIOFIN", "JSWENERGY", "JSWSTEEL", "JUBLFOOD", 
        "KALYANKJIL", "KAYNES", "KEI", "KFINTECH", "KOTAKBANK", "KPITTECH", "LAURUSLABS", "LICHSGFIN", 
        "LODHA", "LT", "LTF", "LTIM", "LUPIN", "M&M", "M&MFIN", "MANAPPURAM", "MANKIND", "MARICO", 
        "MARUTI", "MAXHEALTH", "MAZDOCK", "MCX", "MFSL", "MGL", "MIDCPNIFTY", "MOTHERSON", "MOTILALOFS", "MPHASIS", 
        "MUTHOOTFIN", "NATIONALUM", "NAUKRI", "NBCC", "NCC", "NESTLEIND", "NHPC", "NIFTYNXT50", "NMDC", "NTPC", 
        "NUVAMA", "NYKAA", "OBEROIRLTY", "OFSS", "ONGC", "PAGEIND", "PAYTM", "PERSISTENT", 
        "PETRONET", "PFC", "PGEL", "PHOENIXLTD", "PIDILITIND", "PIIND", "PNB", "PNBHOUSING", 
        "POLICYBZR", "POLYCAB", "POONAWALLA", "POWERGRID", "POWERINDIA", "PRESTIGE", "PPLPHARMA", "RECLTD", 
        "RELIANCE", "RVNL", "SAIL", "SAMMAANCAP", "SBICARD", "SBILIFE", "SBIN", "SHREECEM", 
        "SHRIRAMFIN", "SIEMENS", "SJVN", "SOLARINDS", "SONACOMS", "SRF", "SUNPHARMA", "SUPREMEIND",
        "SYNGENE", "SWIGGY", "TATACHEM", "TATACOMM", "TATACONSUM", "TATAELXSI", "TATAPOWER", "TATASTEEL", 
        "TATATECH", "TCS", "TECHM", "TIINDIA", "TITAGARH", "TITAN", "TMPV", "TORNTPHARM", 
        "TORNTPOWER", "TRENT", "TVSMOTOR", "UBL", "ULTRACEMCO", "UNIONBANK", "UNITDSPR", 
        "UNOMINDA", "UPL", "VBL", "VEDL", "VOLTAS", "WAAREEENER", "WIPRO", "YESBANK", "ZYDUSLIFE"
    ]
    
    # Strike selection strategy
    STRIKE_OFFSET = int(os.getenv("OPTIONS_STRIKE_OFFSET", "0"))  # ATM = 0, OTM = 1+ 
    
    # Expiry handling
    EXPIRY_DAYS_TO_CLOSE = int(os.getenv("OPTIONS_EXPIRY_DAYS_TO_CLOSE", "-1"))  # -1 = disable, don't auto-close (allow trading through expiry)
    PREFER_WEEKLY = os.getenv("OPTIONS_PREFER_WEEKLY", "False").lower() == "true"  # Prefer monthly contracts (CE only)
    
    # Greeks constraints (delta, gamma, theta) - DISABLED FOR LEARNING MODE
    MAX_DELTA = float(os.getenv("OPTIONS_MAX_DELTA", "0.99"))  # Disabled - accept all delta values (0.99 to pass validation)
    MAX_GAMMA = float(os.getenv("OPTIONS_MAX_GAMMA", "10.0"))  # Disabled - very high threshold (no real impact)
    MIN_THETA = float(os.getenv("OPTIONS_MIN_THETA", "-100.0"))  # Disabled - very low threshold (accept all theta)
    
    # =========================================================================
    # IMPROVED GREEKS EXIT THRESHOLDS (Audit Enhancement)
    # =========================================================================
    # These improvements reduce false positives and add confirmation logic
    
    # 1. DELTA REVERSAL - Confirmation for reduced whipsaw
    DELTA_REVERSAL_THRESHOLD = float(os.getenv("OPTIONS_DELTA_REVERSAL_THRESHOLD", "-0.05"))  # Trigger: delta_change < -0.05
    DELTA_REVERSAL_CONFIRM_CYCLES = int(os.getenv("OPTIONS_DELTA_REVERSAL_CONFIRM_CYCLES", "2"))  # Require 2 consecutive cycles OR rolling avg
    ENABLE_DELTA_ROLLING_AVG = os.getenv("OPTIONS_ENABLE_DELTA_ROLLING_AVG", "true").lower() == "true"  # Use rolling average of last 3 samples
    
    # 2. GAMMA EXPLOSION - DISABLED FOR LEARNING MODE
    GAMMA_MULTIPLIER_THRESHOLD = float(os.getenv("OPTIONS_GAMMA_MULTIPLIER_THRESHOLD", "999.0"))  # Disabled - very high threshold
    GAMMA_ABSOLUTE_CAP = float(os.getenv("OPTIONS_GAMMA_ABSOLUTE_CAP", "10.0"))  # Disabled - very high threshold
    
    # 3. THETA ACCELERATION - DISABLED FOR LEARNING MODE
    THETA_MULTIPLIER_THRESHOLD = float(os.getenv("OPTIONS_THETA_MULTIPLIER_THRESHOLD", "999.0"))  # Disabled - very high threshold
    ENABLE_THETA_PNL_CHECK = os.getenv("OPTIONS_ENABLE_THETA_PNL_CHECK", "false").lower() == "true"  # Disabled
    ENABLE_THETA_DELTA_CHECK = os.getenv("OPTIONS_ENABLE_THETA_DELTA_CHECK", "false").lower() == "true"  # Disabled
    
    # 4. VEGA CRUSH - Dynamic threshold based on entry IV
    VEGA_CRUSH_FIXED_THRESHOLD = float(os.getenv("OPTIONS_VEGA_CRUSH_FIXED_THRESHOLD", "2.0"))  # Fixed: 2% IV change
    ENABLE_VEGA_DYNAMIC_THRESHOLD = os.getenv("OPTIONS_ENABLE_VEGA_DYNAMIC_THRESHOLD", "true").lower() == "true"  # Use IV percentile instead
    VEGA_LOW_IV_THRESHOLD = float(os.getenv("OPTIONS_VEGA_LOW_IV_THRESHOLD", "1.0"))  # 1% in low-IV regimes
    VEGA_HIGH_IV_THRESHOLD = float(os.getenv("OPTIONS_VEGA_HIGH_IV_THRESHOLD", "3.0"))  # 3% in high-IV regimes
    VEGA_IV_REGIME_BOUNDARY = float(os.getenv("OPTIONS_VEGA_IV_REGIME_BOUNDARY", "50.0"))  # Boundary between low/high IV
    
    # IV (Implied Volatility) thresholds
    IV_PERCENTILE_MIN = int(os.getenv("OPTIONS_IV_PERCENTILE_MIN", "30"))  # Min IV percentile for entry
    IV_PERCENTILE_MAX = int(os.getenv("OPTIONS_IV_PERCENTILE_MAX", "90"))  # Max IV percentile for entry
    
    # Risk management - SENTIMENT-DRIVEN: 10% hard SL with TRIAL_SL as primary exit signal
    MAX_LOSS_PER_TRADE = float(os.getenv("OPTIONS_MAX_LOSS_PER_TRADE", "5000"))  # Safety limit (emergency exit)
    STOP_LOSS_PERCENTAGE = float(os.getenv("OPTIONS_STOP_LOSS_PERCENTAGE", "10.0"))  # 10% hard SL (broker STOPLOSS_LIMIT order)
    PROFIT_TARGET_PERCENTAGE = float(os.getenv("OPTIONS_PROFIT_TARGET_PERCENTAGE", "0"))  # NO PROFIT TARGET - let winners run!
    
    # Lot sizing is BUDGET-DRIVEN (not fixed lots).
    # Quantity is calculated by OptionsCapitalConfig.calculate_quantity_for_capital():
    #   affordable_lots = floor(cap_this_trade / (premium × lot_size))
    #   quantity        = affordable_lots × lot_size
    # To trade more lots per alert, increase OPTIONS_CAP_PER_TRADE(_GOOD/_NEUTRAL) in .env.
    # NO_OF_LOTS env var is intentionally NOT used — budget-based sizing is more robust.
    
    # Trailing Exit Strategy - TRIAL_SL with configurable trigger buffer.
    # Buffer compensates for STOPLOSS_MARKET slippage: if the intended lock is 5% and
    # the buffer is 1%, the bot waits for a real 6% gain and pushes the broker trigger
    # near +6% so realized fills are more likely to stay around +5%.
    ENABLE_TRAILING_EXIT = os.getenv("OPTIONS_ENABLE_TRAILING_EXIT", "true").lower() == "true"  # Enable trailing
    TRAILING_BUFFER_PERCENTAGE = float(os.getenv("OPTIONS_TRAILING_BUFFER_PERCENTAGE", "1.0"))  # Extra % added to TRIAL_SL trigger/lock to offset stop-market slippage
    TRAILING_GAIN_THRESHOLD = float(os.getenv("TRAILING_GAIN_THRESHOLD", "10.0"))  # Update SL every 10% gain

    # ── SLIPPAGE MODELING (PAPER) ─────────────────────────────────────────────
    # PAPER books ideal fills (entry=LTP, exit=SL trigger) → PnL is optimistic by ~1 round-trip
    # spread. When ON, PAPER books entry at the REAL ask and exit at the REAL bid (fetched live),
    # so PAPER PnL ≈ LIVE PnL. Always logs entry/exit slippage metadata for analysis regardless.
    # REVERTED: market-based fills (no ask/bid simulation) — proved more successful
    PAPER_SLIPPAGE_MODELING = os.getenv("OPTIONS_PAPER_SLIPPAGE_MODELING", "false").lower() == "true"
    # Real-spread entry gate (uses real bid/ask, not synthetic). Advisory by default: logs
    # "would-reject" without blocking, so we gather the spread distribution before enforcing.
    MAX_ENTRY_SPREAD_PCT = float(os.getenv("OPTIONS_MAX_ENTRY_SPREAD_PCT", "5.0"))
    ENTRY_SPREAD_GATE_ENFORCE = os.getenv("OPTIONS_ENTRY_SPREAD_GATE_ENFORCE", "false").lower() == "true"
    TRIAL_SL_SCALP_PREMIUM_MAX = float(os.getenv("OPTIONS_TRIAL_SL_SCALP_PREMIUM_MAX", "12.0"))
    TRIAL_SL_SCALP_ACTIVATION_PCT = float(os.getenv("OPTIONS_TRIAL_SL_SCALP_ACTIVATION_PCT", "5.0"))
    TRIAL_SL_SCALP_GAP = float(os.getenv("OPTIONS_TRIAL_SL_SCALP_GAP", "1.5"))
    TRIAL_SL_STANDARD_GAP = float(os.getenv("OPTIONS_TRIAL_SL_STANDARD_GAP", "2.0"))
    TRIAL_SL_RUNNER_TREND_MIN = float(os.getenv("OPTIONS_TRIAL_SL_RUNNER_TREND_MIN", "0.45"))
    TRIAL_SL_RUNNER_GAP = float(os.getenv("OPTIONS_TRIAL_SL_RUNNER_GAP", "2.5"))
    # Trail-ARM cap: arm the trail earlier so the +3-6% "give-back dead zone" gets protected.
    # With the +1% buffer this means the trail effectively arms at ~4% peak (was ~6%/11%).
    # Lowering the arm does NOT cap upside — the peak-minus-gap trail keeps trailing up.
    TRIAL_SL_BASE_ACTIVATION_PCT = float(os.getenv("OPTIONS_TRIAL_SL_BASE_ACTIVATION_PCT", "3.0"))

    # PROFIT FLOOR (breakeven protection):
    # Once a trade has been green >= TRIGGER%, move the hard SL up to the LOCK% floor and never
    # let stale/dead-trade exits book it below that floor. Kills the "+5% -> -5%" round-trips.
    ENABLE_PROFIT_FLOOR = os.getenv("OPTIONS_ENABLE_PROFIT_FLOOR", "true").lower() == "true"
    PROFIT_FLOOR_TRIGGER_PCT = float(os.getenv("OPTIONS_PROFIT_FLOOR_TRIGGER_PCT", "3.0"))
    PROFIT_FLOOR_LOCK_PCT = float(os.getenv("OPTIONS_PROFIT_FLOOR_LOCK_PCT", "0.0"))
    
    # Signal filtering
    MIN_CONFIDENCE = float(os.getenv("OPTIONS_MIN_CONFIDENCE", "90"))  # Min 90% confidence for options signals
    MIN_SIGNAL_QUALITY = float(os.getenv("OPTIONS_MIN_SIGNAL_QUALITY", "90"))  # Min 90% signal quality score
    MIN_CONFIDENCE_PRE_BREAKOUT = float(os.getenv("OPTIONS_MIN_CONFIDENCE_PRE_BREAKOUT", "55"))
    MIN_CONFIDENCE_PULLBACK = float(os.getenv("OPTIONS_MIN_CONFIDENCE_PULLBACK", "60"))
    MIN_CONFIDENCE_MOMENTUM = float(os.getenv("OPTIONS_MIN_CONFIDENCE_MOMENTUM", "70"))
    # MOMENTUM_CONTINUATION was unmapped → fell through to default MIN_CONFIDENCE (90),
    # silently rejecting ~91% of CE continuation alerts (pine stamps them at 80, weak tier 75).
    # 80 admits the standard tier, still screens the 75 weak tier.
    MIN_CONFIDENCE_MOMENTUM_CONTINUATION = float(os.getenv("OPTIONS_MIN_CONFIDENCE_MOMENTUM_CONTINUATION", "80"))
    MIN_CONFIDENCE_MACD_REVERSAL = float(os.getenv("OPTIONS_MIN_CONFIDENCE_MACD_REVERSAL", "85"))
    MIN_CONFIDENCE_DEEP_MACD_REVERSAL = float(os.getenv("OPTIONS_MIN_CONFIDENCE_DEEP_MACD_REVERSAL", "65"))
    MIN_CONFIDENCE_MOMENTUM_ACCELERATION = float(os.getenv("OPTIONS_MIN_CONFIDENCE_MOMENTUM_ACCELERATION", "80"))

# =============================================================================
# Monitoring Configuration (Faster for IV Decay)
# =============================================================================

class MonitoringConfig:
    """Position monitoring configuration - IV decays FAST, so monitor more frequently"""
    
    # Base monitoring intervals (shorter than equity bot due to IV decay)
    # Options premiums move 2-3x faster than equity, so need sub-5s monitoring
    # With 60s LTP cache, can monitor every 3s without API exhaustion
    MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL", "2"))  # 2s — fast exit-trigger detection

    # Adaptive intervals — PINNED to 2s. Post per-endpoint-limiter fix, LTP for all active
    # strikes is one cheap bulk call (~120/min across 4 bots vs 450/min budget) and the MACD-fade
    # candle check is 5-min-cached, so 2s adds no candle load. Loop interval = exit-trigger
    # DETECTION latency, so 2s minimizes exit slippage. The rate limiter remains the hard guardrail
    # (denies on breach; monitor handles partial fetches), so no auto-backoff is needed.
    MONITOR_INTERVAL_FAST = 2       # rate limits healthy
    MONITOR_INTERVAL_NORMAL = 2     # normal (was 3) — pinned 2s for tight exit detection
    MONITOR_INTERVAL_SLOW = 2       # was 5 — pinned 2s; limiter guards breaches, not the interval
    
    # Rate limiter utilization thresholds for adaptive adjustment
    RATE_LIMIT_HEALTHY_THRESHOLD = 0.50     # < 50% utilization = FAST
    RATE_LIMIT_NORMAL_THRESHOLD = 0.75      # < 75% utilization = NORMAL
    # >= 75% utilization = SLOW
    
    # SENTIMENT CHECK INTERVAL (separate from position monitoring)
    # IV changes fast due to profit booking (every 5-10 seconds)
    # Need frequent checks to catch sentiment fades early
    SENTIMENT_CHECK_INTERVAL_SECONDS = int(os.getenv("SENTIMENT_CHECK_INTERVAL", "5"))  # Check every 5 seconds!

# =============================================================================
# Webhook Configuration (Same port different endpoint)
# =============================================================================

class WebhookConfig:
    """Webhook configuration for options alerts"""
    
    PORT = int(os.getenv("OPTIONS_WEBHOOK_PORT", "8081"))  # Options on 8081, equity on 8080
    HOST = os.getenv("OPTIONS_WEBHOOK_HOST", "127.0.0.1")
    ENDPOINT = "/webhook/options"
    
    # Alert validation
    TIMEOUT = 30  # seconds
    MAX_RETRIES = 3
    
    # Safe mode: validate all alerts before processing
    SAFE_MODE = os.getenv("OPTIONS_SAFE_MODE", "True").lower() == "true"

# =============================================================================
# Development Configuration
# =============================================================================

class DevConfig:
    """Development and testing configuration"""
    
    # Keep legacy flag aligned with the actual configured trading mode.
    PAPER_TRADING_ENABLED = OptionsTradingConfig.TRADING_MODE != "LIVE"
    
    # Debug logging
    DEBUG = os.getenv("OPTIONS_DEBUG", "True").lower() == "true"  # Extensive debugging
    
    # Crash recovery
    ENABLE_CRASH_RECOVERY = os.getenv("OPTIONS_CRASH_RECOVERY", "True").lower() == "true"


def build_empty_live_data(*, market_status: str = "CLOSED") -> Dict[str, Any]:
    """Build a reset live-data payload from the active configuration."""
    return {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "trading_mode": OptionsTradingConfig.TRADING_MODE,
        "market_status": market_status,
        "index_summary": {
            "budget_used": 0.0,
            "trades_today": 0,
            "trade_limit": OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
            "trade_slots_remaining": OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
            "ongoing_trades": 0,
            "closed_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate_percent": 0.0,
            "total_pnl": 0.0,
            "total_pnl_percent": 0.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
        },
        "non_index_summary": {
            "budget_used": 0.0,
            "trades_today": 0,
            "trade_limit": OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
            "trade_slots_remaining": OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
            "ongoing_trades": 0,
            "closed_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate_percent": 0.0,
            "total_pnl": 0.0,
            "total_pnl_percent": 0.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
        },
        "trades": [],
    }


def get_market_status(now: Optional[datetime] = None) -> str:
    """Return OPEN/CLOSED based on configured market hours."""
    current_dt = now or datetime.now()
    market_open = dt_time.fromisoformat(os.getenv("OPTIONS_MARKET_OPEN", "09:15"))
    market_close = dt_time.fromisoformat(os.getenv("OPTIONS_MARKET_CLOSE", "15:15"))
    current_time = current_dt.time()
    return "OPEN" if market_open <= current_time < market_close else "CLOSED"

# =============================================================================
# ML Configuration - Greeks Analysis & Signal Validation
# =============================================================================

class MLConfig:
    """Machine Learning configuration for Greeks-based trading and signal validation"""
    
    # =========================================================================
    # OPTIMAL GREEKS FOR EACH STRATEGY (Initial baseline)
    # =========================================================================
    # These values represent the ideal Greeks profile for each action.
    # ML learning will update these daily based on winning trades.
    # Format: delta_optimal, gamma_optimal, theta_optimal, vega_optimal, iv_range
    
    OPTIMAL_GREEKS = {
        'ce_buy': {
            'delta': float(os.getenv("ML_CE_BUY_DELTA", "0.65")),      # ITM call for directional bullish
            'gamma': float(os.getenv("ML_CE_BUY_GAMMA", "0.015")),     # Low gamma (stable)
            'theta': float(os.getenv("ML_CE_BUY_THETA", "-0.05")),     # Small negative theta (decay against us)
            'vega': float(os.getenv("ML_CE_BUY_VEGA", "0.8")),         # High vega (benefits from IV rise)
        },
        'ce_sell': {
            'delta': float(os.getenv("ML_CE_SELL_DELTA", "-0.35")),    # OTM call for income collection
            'gamma': float(os.getenv("ML_CE_SELL_GAMMA", "-0.015")),   # Low gamma (stable)
            'theta': float(os.getenv("ML_CE_SELL_THETA", "0.05")),     # Positive theta (decay for us)
            'vega': float(os.getenv("ML_CE_SELL_VEGA", "-0.8")),       # Negative vega (benefits from IV drop)
        },
        'pe_buy': {
            'delta': float(os.getenv("ML_PE_BUY_DELTA", "-0.65")),     # ITM put for directional bearish
            'gamma': float(os.getenv("ML_PE_BUY_GAMMA", "0.015")),     # Low gamma (stable)
            'theta': float(os.getenv("ML_PE_BUY_THETA", "-0.05")),     # Small negative theta (decay against us)
            'vega': float(os.getenv("ML_PE_BUY_VEGA", "0.8")),         # High vega (benefits from IV rise)
        },
        'pe_sell': {
            'delta': float(os.getenv("ML_PE_SELL_DELTA", "0.35")),     # OTM put for income collection
            'gamma': float(os.getenv("ML_PE_SELL_GAMMA", "-0.015")),   # Low gamma (stable)
            'theta': float(os.getenv("ML_PE_SELL_THETA", "0.05")),     # Positive theta (decay for us)
            'vega': float(os.getenv("ML_PE_SELL_VEGA", "-0.8")),       # Negative vega (benefits from IV drop)
        },
    }
    
    # =========================================================================
    # GREEKS WEIGHTS FOR SCORING
    # =========================================================================
    # How much each Greek contributes to overall quality score
    
    GREEKS_WEIGHTS = {
        'delta': float(os.getenv("ML_WEIGHT_DELTA", "0.35")),   # 35% - Directional exposure (most important)
        'gamma': float(os.getenv("ML_WEIGHT_GAMMA", "0.20")),   # 20% - Acceleration/risk management
        'theta': float(os.getenv("ML_WEIGHT_THETA", "0.25")),   # 25% - Time decay benefit (income)
        'vega': float(os.getenv("ML_WEIGHT_VEGA", "0.20")),     # 20% - Volatility exposure
    }
    
    # =========================================================================
    # ML CONFIDENCE WEIGHTS FOR MULTI-FACTOR SCORING
    # =========================================================================
    # How much each factor contributes to final ML confidence (0.0 to 1.0)
    
    CONFIDENCE_WEIGHTS = {
        'greeks_quality': float(os.getenv("ML_CONF_GREEKS", "0.35")),          # 35% - Greeks alignment
        'volatility_regime': float(os.getenv("ML_CONF_REGIME", "0.25")),       # 25% - IV regime fit
        'probability_of_profit': float(os.getenv("ML_CONF_POP", "0.25")),      # 25% - PoP from broker
        'contract_type_alignment': float(os.getenv("ML_CONF_CONTRACT", "0.15")),  # 15% - CE/PE match
    }
    
    # Fallback confidence values
    HIGH_CONFIDENCE_FALLBACK = float(os.getenv("ML_HIGH_CONFIDENCE", "0.9"))   # Good regime fit
    
    # =========================================================================
    # NEURAL ML CANDLE FETCHING (API RATE LIMIT FIX)
    # =========================================================================
    # Disable to reduce broker API calls during high-volume alert processing
    # Each alert would otherwise fetch 20 candles = 20 API calls per alert
    # With 100+ alerts = 2,000+ API calls = rate limit exceeded
    # Keep disabled until we implement smart candle caching
    FETCH_CANDLES_FOR_NEURAL_ML = False  # DISABLED to prevent rate limiting (Option A fix)
    MEDIUM_CONFIDENCE_FALLBACK = float(os.getenv("ML_MEDIUM_CONFIDENCE", "0.5"))  # Neutral fit
    DEFAULT_CONFIDENCE = float(os.getenv("ML_DEFAULT_CONFIDENCE", "0.5"))      # No data available
    
    # =========================================================================
    # GREEKS QUALITY SCORING THRESHOLDS
    # =========================================================================
    # How far from optimal is acceptable before rejecting the setup
    
    GREEKS_TOLERANCE_PERCENT = float(os.getenv("ML_GREEKS_TOLERANCE", "20"))   # Accept if within 20% of optimal
    # Example: CE_BUY delta optimal=0.65, tolerance=20% → Accept if 0.52-0.78
    
    GREEKS_QUALITY_EXCELLENT = float(os.getenv("ML_QUALITY_EXCELLENT", "0.85"))  # >85% match = excellent
    GREEKS_QUALITY_GOOD = float(os.getenv("ML_QUALITY_GOOD", "0.70"))            # >70% match = good
    GREEKS_QUALITY_ACCEPTABLE = float(os.getenv("ML_QUALITY_ACCEPTABLE", "0.50")) # >50% match = acceptable
    # Below 50% = rejected
    
    # =========================================================================
    # LEARNING & DAILY UPDATES
    # =========================================================================
    
    ENABLE_EOD_LEARNING = os.getenv("ML_ENABLE_EOD_LEARNING", "True").lower() == "true"
    EOD_LEARNING_HOUR = int(os.getenv("ML_EOD_HOUR", "15"))                     # Run at 15:00 (3 PM)
    EOD_LEARNING_MINUTE = int(os.getenv("ML_EOD_MINUTE", "15"))                 # Run at 15:15 (3:15 PM)
    
    # Minimum trades required before learning updates
    MIN_TRADES_FOR_LEARNING = int(os.getenv("ML_MIN_TRADES_FOR_LEARNING", "5"))
    
    # History window for learning (keep last N trades)
    TRADE_HISTORY_SIZE = int(os.getenv("ML_HISTORY_SIZE", "100"))               # Learn from last 100 trades
    
    # =========================================================================
    # ML SIGNAL FILTERING
    # =========================================================================
    
    ENABLE_ML_FILTERING = os.getenv("ML_ENABLE_FILTERING", "True").lower() == "true"
    MIN_ML_CONFIDENCE_FOR_ENTRY = float(os.getenv("ML_MIN_CONFIDENCE", "0.50"))  # Need 50%+ confidence
    # Below 50% confidence → Alert might be a false signal, skip
    
    # Maximum trades to process from queue
    MAX_TRADES_PER_ML_CHECK = int(os.getenv("ML_MAX_TRADES_PER_CHECK", "3"))    # Process top 3 by confidence
    
    # =========================================================================
    # FEATURE ENGINEERING (Phase 2+)
    # =========================================================================
    
    # Greeks change calculation
    ENABLE_GREEKS_DELTA_FEATURES = os.getenv("ML_ENABLE_DELTA_FEATURES", "True").lower() == "true"
    # Calculate: delta_change = exit_delta - entry_delta
    # Use to understand directional movement impact
    
    ENABLE_IV_FEATURES = os.getenv("ML_ENABLE_IV_FEATURES", "True").lower() == "true"
    # Calculate: iv_percentile, iv_rank, iv_change
    # Use to understand volatility regime
    
    # =========================================================================
    # LOGGING & DEBUGGING
    # =========================================================================
    
    LOG_GREEKS_SCORES = os.getenv("ML_LOG_GREEKS", "True").lower() == "true"
    LOG_CONFIDENCE_CALC = os.getenv("ML_LOG_CONFIDENCE", "True").lower() == "true"
    LOG_EOD_LEARNING = os.getenv("ML_LOG_EOD", "True").lower() == "true"
    
    # Dump learned models to logs for inspection
    DUMP_LEARNED_GREEKS = os.getenv("ML_DUMP_LEARNED", "False").lower() == "true"
    
    # =========================================================================
    # ML MODEL ENSEMBLE WEIGHTS
    # =========================================================================
    # How much each model contributes to final prediction
    
    MODEL_WEIGHTS = {
        'random_forest': float(os.getenv("ML_WEIGHT_RF", "0.5")),           # 50% - Foundation model
        'gradient_boosting': float(os.getenv("ML_WEIGHT_GB", "0.3")),       # 30% - Gradient boosting
        'svm': float(os.getenv("ML_WEIGHT_SVM", "0.2")),                    # 20% - Support vector machine
    }
    
    # Prediction boundaries
    ML_SCORE_MIN = float(os.getenv("ML_SCORE_MIN", "0.3"))                  # 30% floor for predictions
    ML_SCORE_MAX = float(os.getenv("ML_SCORE_MAX", "0.85"))                 # 85% ceiling (conservative)
    
    # Feature defaults
    DEFAULT_IV_PERCENTILE = int(os.getenv("ML_DEFAULT_IV_PERCENTILE", "50")) # 50th percentile if unknown
    DEFAULT_VOLATILITY = float(os.getenv("ML_DEFAULT_VOLATILITY", "1.0"))   # Neutral volatility
    
    # Signal quality thresholds
    MIN_CONFIDENCE_FOR_TRADE = float(os.getenv("ML_MIN_CONFIDENCE_TRADE", "50.0"))  # Minimum TradingView confidence %
    
    # =========================================================================
    # VALIDATION THRESHOLDS FOR GREEKS (Entry validation)
    # =========================================================================
    # These ranges define acceptable Greeks for each action type
    # Used during alert validation - alerts outside these ranges are rejected
    # ML learning will update these daily based on winning trades
    
    VALIDATION_RANGES = {
        'ce_buy': {
            'delta_min': float(os.getenv("ML_CE_BUY_DELTA_MIN", "0.2")),
            'delta_max': float(os.getenv("ML_CE_BUY_DELTA_MAX", "0.8")),
            'gamma_min': float(os.getenv("ML_CE_BUY_GAMMA_MIN", "0.0")),
            'gamma_max': float(os.getenv("ML_CE_BUY_GAMMA_MAX", "0.05")),
        },
        'ce_sell': {
            'delta_min': float(os.getenv("ML_CE_SELL_DELTA_MIN", "-0.8")),
            'delta_max': float(os.getenv("ML_CE_SELL_DELTA_MAX", "-0.2")),
            'gamma_min': float(os.getenv("ML_CE_SELL_GAMMA_MIN", "-0.05")),
            'gamma_max': float(os.getenv("ML_CE_SELL_GAMMA_MAX", "0.0")),
        },
        'pe_buy': {
            'delta_min': float(os.getenv("ML_PE_BUY_DELTA_MIN", "-0.8")),
            'delta_max': float(os.getenv("ML_PE_BUY_DELTA_MAX", "-0.2")),
            'gamma_min': float(os.getenv("ML_PE_BUY_GAMMA_MIN", "0.0")),
            'gamma_max': float(os.getenv("ML_PE_BUY_GAMMA_MAX", "0.05")),
        },
        'pe_sell': {
            'delta_min': float(os.getenv("ML_PE_SELL_DELTA_MIN", "0.2")),
            'delta_max': float(os.getenv("ML_PE_SELL_DELTA_MAX", "0.8")),
            'gamma_min': float(os.getenv("ML_PE_SELL_GAMMA_MIN", "-0.05")),
            'gamma_max': float(os.getenv("ML_PE_SELL_GAMMA_MAX", "0.0")),
        },
    }

# =============================================================================
# F&O Universe Utilities
# =============================================================================

class FOUniverseUtils:
    """Utilities for working with F&O universe and deriving strikes"""
    
    @staticmethod
    def get_fo_symbols() -> list:
        """Get all F&O universe symbols"""
        return OptionsTradingConfig.FO_UNIVERSE
    
    @staticmethod
    def get_symbol_count() -> int:
        """Get count of F&O universe symbols"""
        return len(OptionsTradingConfig.FO_UNIVERSE)
    
    @staticmethod
    def is_in_fo_universe(symbol: str) -> bool:
        """Check if symbol is in F&O universe"""
        # Normalize symbol (remove NSE: prefix if present, handle case variations)
        clean_symbol = symbol.replace("NSE:", "").upper()
        return clean_symbol in [s.upper() for s in OptionsTradingConfig.FO_UNIVERSE]
    
    @staticmethod
    def derive_strikes(current_price: float, strike_step: int = 100, atm_offset: int = 0) -> Dict[str, float]:
        """
        Derive option strike prices for a given underlying price
        
        Args:
            current_price: Current LTP of underlying
            strike_step: Strike interval (typically 100 for stock options, 50 for indices)
            atm_offset: Offset for ATM selection (0=ATM, -1=OTM CE, +1=OTM PE)
        
        Returns:
            Dictionary with strike prices: {'ATM': price, 'CE_OTM1': price, 'CE_OTM2': price, ...}
        """
        # Find ATM strike
        atm = (int(current_price / strike_step) + atm_offset) * strike_step
        
        strikes = {
            'ATM': float(atm),
            'CE_OTM1': float(atm + strike_step),      # +1 strike CE (OTM call)
            'CE_OTM2': float(atm + 2 * strike_step),  # +2 strikes CE
            'PE_OTM1': float(atm - strike_step),      # -1 strike PE (OTM put)
            'PE_OTM2': float(atm - 2 * strike_step),  # -2 strikes PE
        }
        return strikes
    
    @staticmethod
    def get_strike_range(current_price: float, num_strikes: int = 3, strike_step: int = 100) -> Dict[str, list]:
        """
        Get range of strikes around current price
        
        Args:
            current_price: Current LTP
            num_strikes: Number of strikes on each side of ATM
            strike_step: Strike interval
        
        Returns:
            Dictionary with lists of CE and PE strikes
        """
        atm = (int(current_price / strike_step)) * strike_step
        
        calls = [float(atm + (i * strike_step)) for i in range(num_strikes + 1)]
        puts = [float(atm - (i * strike_step)) for i in range(num_strikes + 1)]
        
        return {
            'calls': calls,
            'puts': puts,
            'atm': float(atm)
        }

# =============================================================================
# Utilities
# =============================================================================

def get_optconfig_summary() -> Dict[str, Any]:
    """Get summary of all option bot configuration"""
    return {
        "capital": {
            "max_capital": OptionsCapitalConfig.MAX_CAPITAL,
            "cap_per_trade": OptionsCapitalConfig.CAP_PER_TRADE,
            "reserve": OptionsCapitalConfig.RESERVE_CAPITAL
        },
        "trading": {
            "mode": OptionsTradingConfig.TRADING_MODE,
            "underlyings": OptionsTradingConfig.UNDERLYING_INDEXES,
            "strike_offset": OptionsTradingConfig.STRIKE_OFFSET,
            "max_delta": OptionsTradingConfig.MAX_DELTA,
            "iv_range": [OptionsTradingConfig.IV_PERCENTILE_MIN, OptionsTradingConfig.IV_PERCENTILE_MAX]
        },
        "fo_universe": {
            "symbol_count": FOUniverseUtils.get_symbol_count(),
            "symbols_sample": OptionsTradingConfig.FO_UNIVERSE[:10],
            "total_symbols": len(OptionsTradingConfig.FO_UNIVERSE)
        },
        "monitoring": {
            "default_interval": MonitoringConfig.MONITOR_INTERVAL_SECONDS,
            "intervals": {
                "fast": MonitoringConfig.MONITOR_INTERVAL_FAST,
                "normal": MonitoringConfig.MONITOR_INTERVAL_NORMAL,
                "slow": MonitoringConfig.MONITOR_INTERVAL_SLOW
            }
        },
        "webhook": {
            "port": WebhookConfig.PORT,
            "endpoint": WebhookConfig.ENDPOINT
        },
        "paper_trading": DevConfig.PAPER_TRADING_ENABLED
    }

# =============================================================================
# Market Sentiment Configuration (PCR + OI Buildup)
# =============================================================================

class SentimentConfig:
    """PCR and OI Buildup thresholds for entry and exit decisions"""
    
    # =========================================================================
    # ENTRY Thresholds (LOOSE - prioritize not missing moves)
    # =========================================================================
    
    # PCR range for entry: wider range to catch moves early
    # FEB 23 FIX: Adjusted to handle bullish market (low PCR = high call demand)
    ENTRY_PCR_MIN = 0.10       # Super bullish market (PCR 0.10 = 10 puts per 100 calls)
    ENTRY_PCR_MAX = 2.0        # Even bearish OK to scalp counter-trades (loose upper limit)
    
    # OI Buildup confirmation for entry (optional)
    CHECK_OI_BUILDUP_ON_ENTRY = True
    ENTRY_OI_BUILDUP_MIN = 500_000  # Loose threshold - any meaningful buildup adds confidence
    
    # =========================================================================
    # EXIT Thresholds (STRICT - exit when sentiment FADES from entry)
    # =========================================================================
    # IMPORTANT: Exits are based on PERCENTAGE CHANGE from entry levels,
    # not absolute thresholds. This ensures we exit when conviction weakens,
    # regardless of entry conditions.
    
    # PCR Fade thresholds: % change from entry level
    # Example for CE: Entry PCR 0.9, exit if rises 20% (0.9 * 1.20 = 1.08)
    # Example for PE: Entry PCR 1.1, exit if drops 20% (1.1 * 0.80 = 0.88)
    EXIT_PCR_FADE_THRESHOLD = 20      # Exit if PCR changes 20% from entry (5-30% range tunable)
    
    # OI Buildup Fade threshold: % drop from entry level indicates conviction weakening
    # Example: Entry OI 5M, exit if drops 40% (5M * 0.60 = 3M)
    EXIT_OI_FADE_THRESHOLD = 40       # Exit if OI drops 40% from entry (20-60% range tunable)
    
    # Note: Removed old absolute thresholds (EXIT_PCR_BEARISH=1.5, EXIT_PCR_BULLISH=0.4, 
    # EXIT_OI_THRESHOLD=100k) as they don't adapt to entry conditions and miss fades.
    
    # Short Covering monitoring (indicates weakness)
    CHECK_SHORT_COVERING_ON_EXIT = True
    EXIT_SHORT_COVERING_THRESHOLD = 1_000_000  # If shorts are covering heavily, exit
    
    # =========================================================================
    # Feature Flags
    # =========================================================================
    
    ENABLE_SENTIMENT_FILTER = False           # Global toggle for sentiment checks - DISABLED FOR LEARNING MODE (Option A)
    ENABLE_SENTIMENT_EXIT = False            # Disable fade-based exit detection for learning mode
    LOG_SENTIMENT_CHECKS = True              # Log all sentiment decisions
    ALERT_ON_SENTIMENT_CHANGE = True         # Send alerts when sentiment changes
    
    # =========================================================================
    # Liquidity Threshold (Minimum OI for entry)
    # =========================================================================
    
    CHECK_MIN_OI_ON_ENTRY = True             # Verify minimum liquidity (OI) before entry
    MIN_OI_LIQUIDITY_THRESHOLD = 100_000     # Skip contracts with OI < 100K (prevents illiquid traps)
    # Example: BANKNIFTY 51000 CE with OI=50K (illiquid) → REJECTED
    #          BANKNIFTY 51000 CE with OI=120K (liquid) → ACCEPTED
    
    # =========================================================================
    # Early Exit - Momentum Reversal Detection (Post-Entry Protection)
    # =========================================================================
    
    ENABLE_EARLY_EXIT_MOMENTUM = False       # Exit early if momentum reverses post-entry (DISABLED - leaving too much on table)
    EARLY_EXIT_MOMENTUM_THRESHOLD = 10.0     # Exit if price drops >10% from peak (catches 75% of hard SLs)
    # Example: Entry ₹100 → Peak ₹104 → Current ₹93.6 (10% below peak) → EXIT
    # Impact: Saves 69% of losses vs waiting for hard SL (-20%)
    # False positive rate: ~3-5% on winners (acceptable)

    ENABLE_CANDLE_MACD_FADE_EXIT = os.getenv("ENABLE_CANDLE_MACD_FADE_EXIT", "false").strip().lower() == "true"
    # Keep disabled by default: the current implementation relies on completed candle snapshots,
    # which is too coarse for live option premium exits unless rebuilt on real-time data.
    CANDLE_MACD_FADE_MIN_SECONDS = 180       # Give trades 3 minutes before evaluating real fade vs noise
    CANDLE_MACD_FADE_MIN_DRAWDOWN = 4.0      # Ignore shallow pullbacks; require at least 4% option drawdown from peak
    CANDLE_MACD_FADE_MAX_PROFIT_PCT = 4.0    # If trade is still comfortably green, let TRIAL_SL manage it
    CANDLE_MACD_FADE_BASE_SCORE = 3          # Minimum confirmation score before early exit
    CANDLE_MACD_FADE_MACD_DECAY_RATIO = 0.35 # Current MACD histogram must decay well below entry impulse
    CANDLE_MACD_FADE_STRONG_ADX = 28.0       # Strong trend needs extra confirmation to avoid normal pullback exits
    CANDLE_MACD_FADE_HIGH_VOLUME_RATIO = 1.8 # High-volume breakouts deserve more room
    CANDLE_MACD_FADE_STRONG_EMA_SPREAD = 0.8 # Wide EMA spread suggests trend still healthy
    CANDLE_MACD_FADE_HIGH_ATR_PC = 2.0       # High ATR implies noisier pullbacks; raise exit bar
    CANDLE_MACD_FADE_RSI_EXHAUSTION_CE = 72.0
    CANDLE_MACD_FADE_RSI_EXHAUSTION_PE = 28.0

    ENABLE_REALTIME_PREMIUM_FADE_EXIT = os.getenv("ENABLE_REALTIME_PREMIUM_FADE_EXIT", "false").strip().lower() == "true"
    REALTIME_PREMIUM_FADE_CHECK_INTERVAL_SECONDS = int(os.getenv("REALTIME_PREMIUM_FADE_CHECK_INTERVAL_SECONDS", "10"))
    REALTIME_PREMIUM_FADE_MIN_SECONDS = int(os.getenv("REALTIME_PREMIUM_FADE_MIN_SECONDS", "120"))
    REALTIME_PREMIUM_FADE_MIN_HISTORY_POINTS = int(os.getenv("REALTIME_PREMIUM_FADE_MIN_HISTORY_POINTS", "7"))
    REALTIME_PREMIUM_FADE_MIN_DRAWDOWN = float(os.getenv("REALTIME_PREMIUM_FADE_MIN_DRAWDOWN", "3.0"))
    REALTIME_PREMIUM_FADE_MAX_PROFIT_PCT = float(os.getenv("REALTIME_PREMIUM_FADE_MAX_PROFIT_PCT", "6.0"))
    REALTIME_PREMIUM_FADE_BASE_SCORE = int(os.getenv("REALTIME_PREMIUM_FADE_BASE_SCORE", "4"))
    REALTIME_PREMIUM_FADE_MACD_FAST = int(os.getenv("REALTIME_PREMIUM_FADE_MACD_FAST", "8"))
    REALTIME_PREMIUM_FADE_MACD_SLOW = int(os.getenv("REALTIME_PREMIUM_FADE_MACD_SLOW", "21"))
    REALTIME_PREMIUM_FADE_MACD_SIGNAL = int(os.getenv("REALTIME_PREMIUM_FADE_MACD_SIGNAL", "5"))
    REALTIME_PREMIUM_FADE_PEAK_REVERSION_PCT = float(os.getenv("REALTIME_PREMIUM_FADE_PEAK_REVERSION_PCT", "1.8"))
    REALTIME_PREMIUM_FADE_REBOUND_FAILURE_PCT = float(os.getenv("REALTIME_PREMIUM_FADE_REBOUND_FAILURE_PCT", "0.6"))
    REALTIME_PREMIUM_FADE_DECAY_BUFFER_MULTIPLIER = float(os.getenv("REALTIME_PREMIUM_FADE_DECAY_BUFFER_MULTIPLIER", "1.5"))
    REALTIME_PREMIUM_FADE_STRONG_ADX = float(os.getenv("REALTIME_PREMIUM_FADE_STRONG_ADX", "28.0"))
    REALTIME_PREMIUM_FADE_HIGH_VOLUME_RATIO = float(os.getenv("REALTIME_PREMIUM_FADE_HIGH_VOLUME_RATIO", "1.8"))
    
    # =========================================================================
    # Early Exit - IV Crash Detection (Premium Collapse Protection)
    # =========================================================================
    
    ENABLE_EARLY_EXIT_IV_CRASH = True        # Exit early if IV collapses (premium dies)
    EARLY_EXIT_IV_CRASH_THRESHOLD = 10.0     # Exit if IV drops >10% from entry (no recovery potential)
    # Rationale: IV crash = premium is dying = no point staying
    # This catches the root cause that triggers MOMENTUM_REVERSAL
    # Earlier signal than momentum (IV crashes before big reversals)
    # Expected impact: Save ₹20-30k on choppy/reversal days
    
    ENABLE_EARLY_EXIT_IV_SPIKE = True        # Exit early if IV spikes (fear spike = market crash)
    EARLY_EXIT_IV_SPIKE_THRESHOLD = 15.0     # Exit if IV rises >15% from entry (market panic)
    EARLY_EXIT_IV_SPIKE_MIN_TIME = 5         # Minimum seconds in position before checking IV spike
    # Rationale: IV spike (opposite of crash) signals market panic/crash in progress
    # On crash days: IV spikes BEFORE price drops (fear before selling)
    # Catches crashes 1-2 minutes earlier than MOMENTUM_REVERSAL
    # Jan 8 example: IV spiked during -1.04% crash; momentum caught after -10% loss
    # Expected impact: Exit with 1-2min earlier signal on crash days
    
    # =========================================================================
    # PCR Data Retry Logic (for brief market data lags)
    # =========================================================================
    
    PCR_RETRY_ENABLED = True                 # Retry PCR fetch if data temporarily unavailable
    PCR_RETRY_MAX_ATTEMPTS = 3               # Number of retry attempts (1 initial + 2 retries = 3 total)
    PCR_RETRY_DELAY_SECONDS = 1              # Delay between retries in seconds
    # Total wait time: 2 seconds (2 retries × 1s delay, not counting initial fetch)
    
    # =========================================================================
    # API Call Frequency & Performance
    # =========================================================================
    
    CACHE_DURATION_SECONDS = 300             # Cache PCR/OI for 5 minutes
    REFRESH_ON_POSITION_ENTRY = True         # Refresh sentiment data on every entry
    REFRESH_ON_POSITION_EXIT = True          # Refresh sentiment data on exit checks
    SENTIMENT_CHECK_INTERVAL_SECONDS = 60    # Check sentiment every 60 seconds during holding

def validate_optconfig() -> Tuple[bool, str]:
    """Validate options bot configuration"""
    # In PAPER mode, API credentials are optional for testing
    if not DevConfig.PAPER_TRADING_ENABLED and not AngelOneConfig.API_KEY:
        return False, "ANGEL_API_KEY required for LIVE trading"
    
    # Always validate capital and Greeks constraints
    if OptionsCapitalConfig.MAX_CAPITAL <= 0:
        return False, "OPTIONS_MAX_CAPITAL must be > 0"
    if OptionsTradingConfig.MAX_DELTA >= 1.0:
        return False, "OPTIONS_MAX_DELTA must be < 1.0"
    
    # Validate sentiment thresholds
    if SentimentConfig.ENTRY_PCR_MIN >= SentimentConfig.ENTRY_PCR_MAX:
        return False, "ENTRY_PCR_MIN must be < ENTRY_PCR_MAX"
    # EXIT_PCR thresholds were refactored to use EXIT_PCR_FADE_THRESHOLD
    # No need to validate old absolute thresholds
    
    return True, "Options bot configuration valid"
