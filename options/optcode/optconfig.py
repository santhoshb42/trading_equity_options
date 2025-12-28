"""
Options Trading Bot Configuration Module

All AngelOne parameters, budget settings, and options-specific configuration.
Completely independent from equity bot, shares only webhook alerts from TradingView.
"""

import os
from typing import Dict, Any, Tuple
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

# Environment file
ENV_FILE = BASE_DIR / ".env"

# Load environment variables from .env file if dotenv is available
if _DOTENV_AVAILABLE and ENV_FILE.exists():
    load_dotenv(ENV_FILE)

# Data files
INSTRUMENT_FILE = BASE_DIR / "tools" / "instrument.json"
SESSION_FILE = BASE_DIR / "data" / "session.json"
POSITIONS_FILE = BASE_DIR / "data" / "positions.json"
OPTION_CHAIN_CACHE = BASE_DIR / "data" / "option_chain_cache.json"

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
    
    # Maximum concurrent positions (30 slots for aggressive options trading)
    MAX_SLOTS = int(os.getenv("OPTIONS_MAX_SLOTS", "30"))  # Max 30 concurrent option positions
    
    # Maximum trades per day (HARDCODED - Do NOT exceed this limit)
    MAX_TRADES_PER_DAY = int(os.getenv("OPTIONS_MAX_TRADES_PER_DAY", "30"))  # Max 30 TOTAL trades per day (not concurrent)
    
    # Reserve capital (emergency buffer for options)
    RESERVE_CAPITAL = float(os.getenv("OPTIONS_RESERVE_CAPITAL", "50000"))  # ₹50,000 reserve
    
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
        Calculate option contracts (lots) to maximize capital utilization.
        
        Formula: quantity = (capital / premium) * lot_size
        
        Example:
          Budget: ₹30,000
          Premium: ₹6,000
          Lot Size: 75 (BANKNIFTY standard)
          Calculation: 30,000 / 6,000 = 5 lots × 75 = 375 contracts
          Utilization: 100% (5 × 6,000 = 30,000)
        """
        if premium <= 0:
            return 0
        
        # Calculate how many premium units we can afford
        num_lots = int(capital / premium)
        
        # If we can't afford even 1 lot, return minimum (1 lot)
        if num_lots < 1:
            return lot_size  # Return 1 contract (minimum trade size)
        
        # Calculate total quantity = number of premium units × lot size
        quantity = num_lots * lot_size
        
        # Verify we don't exceed capital (safety check)
        actual_cost = (quantity / lot_size) * premium
        if actual_cost > capital:
            # Reduce by 1 lot and recalculate
            quantity = (num_lots - 1) * lot_size if num_lots > 1 else lot_size
        
        return max(lot_size, quantity)
    
    @classmethod
    def get_available_capital(cls, used_capital: float) -> float:
        """Get available capital after reserves"""
        available = cls.MAX_CAPITAL - cls.RESERVE_CAPITAL - used_capital
        return max(0, available)
    
    @classmethod
    def get_daily_trade_count(cls) -> int:
        """Get number of trades placed today (reads from daily state file)"""
        from datetime import datetime
        import json
        
        daily_state_file = BASE_DIR / "data" / f"daily_trades_{datetime.now().strftime('%Y-%m-%d')}.json"
        
        if daily_state_file.exists():
            try:
                with open(daily_state_file) as f:
                    data = json.load(f)
                    return data.get('trades_placed', 0)
            except Exception:
                return 0
        return 0
    
    @classmethod
    def increment_daily_trade_count(cls) -> int:
        """Increment daily trade counter and return new count"""
        from datetime import datetime
        import json
        
        daily_state_file = BASE_DIR / "data" / f"daily_trades_{datetime.now().strftime('%Y-%m-%d')}.json"
        
        try:
            # Read existing count
            count = 0
            if daily_state_file.exists():
                with open(daily_state_file) as f:
                    data = json.load(f)
                    count = data.get('trades_placed', 0)
            
            # Increment and save
            count += 1
            with open(daily_state_file, 'w') as f:
                json.dump({
                    'date': datetime.now().isoformat(),
                    'trades_placed': count,
                    'max_allowed': cls.MAX_TRADES_PER_DAY
                }, f)
            
            return count
        except Exception as e:
            from .optlogging import logger
            logger.error(f"DAILY_TRADE_COUNT: ERROR incrementing | {str(e)}")
            return 0

# =============================================================================
# Options Trading Configuration
# =============================================================================

class OptionsTradingConfig:
    """Options-specific trading strategy and risk management"""
    
    # Trading mode - PAPER mode for testing without real orders
    TRADING_MODE = "PAPER"  # PAPER mode - simulated orders, real data from broker API
    
    # Underlying indexes for options trading (legacy - keep for backward compatibility)
    UNDERLYING_INDEXES = ["BANKNIFTY", "NIFTY", "FINNIFTY"]  # Preferred underlying indexes
    
    # F&O Universe - Complete NSE stock list for deriving strikes
    FO_UNIVERSE = [
        "PEL", "AARTIIND", "ABB", "ABCAPITAL", "ABFRL", "ACC", "ADANIENSOL", "ADANIENT", 
        "ADANIGREEN", "ADANIPORTS", "ALKEM", "AMBER", "AMBUJACEM", "ANGELONE", "APLAPOLLO", 
        "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ASTRAL", "ATGL", "AUBANK", "AUROPHARMA", 
        "AXISBANK", "BAJAJFINSV", "BAJAJ_AUTO", "BAJFINANCE", "BALKRISIND", "BANDHANBNK", "BANKBARODA", 
        "BDL", "BEL", "BHARATFORG", "BHARTIARTL", "BHEL", "BIOCON", "BLUESTARCO", "BOSCHLTD", 
        "BPCL", "BRITANNIA", "BSE", "BSOFT", "CAMS", "CANBK", "CESC", "CGPOWER", "CHAMBLFERT", 
        "CHOLAFIN", "CIPLA", "COALINDIA", "COFORGE", "COLPAL", "CONCOR", "CROMPTON", 
        "CUMMINSIND", "CYIENT", "DABUR", "DALBHARAT", "DELHIVERY", "DIVISLAB", "DIXON", "DLF", 
        "DRREDDY", "EICHERMOT", "ESCORTS", "ETERNAL", "EXIDEIND", "FORTIS", "GAIL", "GLENMARK", 
        "GMRAIRPORT", "GODREJCP", "GODREJPROP", "GRANULES", "GRASIM", "HAL", "HAVELLS", 
        "HCLTECH", "HDFCAMC", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HFCL", "HINDALCO", 
        "HINDCOPPER", "HINDPETRO", "HINDUNILVR", "HINDZINC", "HUDCO", "ICICIBANK", "ICICIGI", 
        "ICICIPRULI", "IDEA", "IDFCFIRSTB", "IEX", "IGL", "IIFL", "INDHOTEL", "INDIANB", 
        "INDIGO", "INDUSINDBK", "INDUSTOWER", "INFY", "INOXWIND", "IOC", "IRB", "IRCTC", 
        "IREDA", "IRFC", "ITC", "JINDALSTEL", "JIOFIN", "JSWENERGY", "JSWSTEEL", "JUBLFOOD", 
        "KALYANKJIL", "KAYNES", "KFINTECH", "KOTAKBANK", "KPITTECH", "LAURUSLABS", "LICHSGFIN", 
        "LODHA", "LT", "LTF", "LTIM", "LUPIN", "M&M", "M&MFIN", "MANAPPURAM", "MANKIND", "MARICO", 
        "MARUTI", "MAXHEALTH", "MAZDOCK", "MCX", "MFSL", "MGL", "MOTHERSON", "MPHASIS", 
        "MUTHOOTFIN", "NATIONALUM", "NAUKRI", "NBCC", "NCC", "NESTLEIND", "NHPC", "NMDC", "NTPC", 
        "NUVAMA", "NYKAA", "OBEROIRLTY", "OFSS", "ONGC", "PAGEIND", "PAYTM", "PERSISTENT", 
        "PETRONET", "PFC", "PGEL", "PHOENIXLTD", "PIDILITIND", "PIIND", "PNB", "PNBHOUSING", 
        "POLICYBZR", "POLYCAB", "POONAWALLA", "POWERGRID", "POWERINDIA", "PRESTIGE", "PPLPHARMA", "RECLTD", 
        "RELIANCE", "RVNL", "SAIL", "SAMMAANCAP", "SBICARD", "SBILIFE", "SBIN", "SHREECEM", 
        "SHRIRAMFIN", "SIEMENS", "SJVN", "SOLARINDS", "SONACOMS", "SRF", "SUNPHARMA", "SUPREMEIND", 
        "SYNGENE", "TATACHEM", "TATACOMM", "TATACONSUM", "TATAELXSI", "TATAPOWER", "TATASTEEL", 
        "TATATECH", "TCS", "TECHM", "TIINDIA", "TITAGARH", "TITAN", "TMPV", "TORNTPHARM", 
        "TORNTPOWER", "TRENT", "TVSMOTOR", "UBL", "ULTRACEMCO", "UNIONBANK", "UNITDSPR", 
        "UNOMINDA", "UPL", "VBL", "VEDL", "VOLTAS", "WIPRO", "ZYDUSLIFE"
    ]
    
    # Strike selection strategy
    STRIKE_OFFSET = int(os.getenv("OPTIONS_STRIKE_OFFSET", "0"))  # ATM = 0, OTM = 1+ 
    
    # Expiry handling
    EXPIRY_DAYS_TO_CLOSE = int(os.getenv("OPTIONS_EXPIRY_DAYS_TO_CLOSE", "1"))  # Close 1 day before expiry
    PREFER_WEEKLY = os.getenv("OPTIONS_PREFER_WEEKLY", "True").lower() == "true"  # Prefer weekly contracts
    
    # Greeks constraints (delta, gamma, theta)
    MAX_DELTA = float(os.getenv("OPTIONS_MAX_DELTA", "0.8"))  # Max delta for position (directional bias)
    MAX_GAMMA = float(os.getenv("OPTIONS_MAX_GAMMA", "0.05"))  # Max gamma (risk from price moves)
    MIN_THETA = float(os.getenv("OPTIONS_MIN_THETA", "-0.01"))  # Min theta (time decay preference)
    
    # IV (Implied Volatility) thresholds
    IV_PERCENTILE_MIN = int(os.getenv("OPTIONS_IV_PERCENTILE_MIN", "30"))  # Min IV percentile for entry
    IV_PERCENTILE_MAX = int(os.getenv("OPTIONS_IV_PERCENTILE_MAX", "90"))  # Max IV percentile for entry
    
    # Risk management - SENTIMENT-DRIVEN: 20% SL with sentiment fade as primary exit signal
    MAX_LOSS_PER_TRADE = float(os.getenv("OPTIONS_MAX_LOSS_PER_TRADE", "5000"))  # Safety limit (emergency exit) - high enough not to interfere with 20% SL
    STOP_LOSS_PERCENTAGE = float(os.getenv("OPTIONS_STOP_LOSS_PERCENTAGE", "20.0"))  # 20% SL (fixed below entry)
    PROFIT_TARGET_PERCENTAGE = float(os.getenv("OPTIONS_PROFIT_TARGET_PERCENTAGE", "0"))  # NO PROFIT TARGET - let winners run!
    
    # Number of lots per trade (for scaling trade size)
    # Each option contract = 1 lot (qty = lot_size * NO_OF_LOTS)
    # When scaling: increase this to increase trade size proportionally
    # Example: NO_OF_LOTS=1 → qty=lot_size, NO_OF_LOTS=2 → qty=2*lot_size
    NO_OF_LOTS = int(os.getenv("NO_OF_LOTS", "1"))  # Default 1 lot per trade
    
    # Trailing Exit Strategy - MOVE SL UP EVERY 10% GAIN
    # Keep SL 20% below peak price as position gains
    # Example: Entry ₹100 (SL=₹80) → Reaches ₹110 (SL=₹88) → Reaches ₹121 (SL=₹96.80)
    ENABLE_TRAILING_EXIT = os.getenv("OPTIONS_ENABLE_TRAILING_EXIT", "true").lower() == "true"  # Enable trailing
    TRAILING_BUFFER_PERCENTAGE = float(os.getenv("OPTIONS_TRAILING_BUFFER_PERCENTAGE", "20.0"))  # Keep 20% below peak
    TRAILING_GAIN_THRESHOLD = float(os.getenv("TRAILING_GAIN_THRESHOLD", "10.0"))  # Update SL every 10% gain
    
    # Signal filtering
    MIN_CONFIDENCE = float(os.getenv("OPTIONS_MIN_CONFIDENCE", "90"))  # Min 90% confidence for options signals
    MIN_SIGNAL_QUALITY = float(os.getenv("OPTIONS_MIN_SIGNAL_QUALITY", "90"))  # Min 90% signal quality score

# =============================================================================
# Monitoring Configuration (Faster for IV Decay)
# =============================================================================

class MonitoringConfig:
    """Position monitoring configuration - IV decays FAST, so monitor more frequently"""
    
    # Base monitoring intervals (shorter than equity bot due to IV decay)
    MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL", "10"))  # Default 10s (vs equity 20s)
    
    # Adaptive intervals based on rate limiter health
    MONITOR_INTERVAL_FAST = 8       # When rate limits are healthy (vs equity 15s) - for fast IV changes
    MONITOR_INTERVAL_NORMAL = 10    # Normal monitoring (vs equity 20s) - standard IV tracking
    MONITOR_INTERVAL_SLOW = 20      # When rate limits are stressed (vs equity 45s) - still faster than equity
    
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
    
    # Paper trading DISABLED - LIVE trading enabled
    PAPER_TRADING_ENABLED = False  # LIVE mode - real trading enabled
    
    # Debug logging
    DEBUG = os.getenv("OPTIONS_DEBUG", "True").lower() == "true"  # Extensive debugging
    
    # Crash recovery
    ENABLE_CRASH_RECOVERY = os.getenv("OPTIONS_CRASH_RECOVERY", "True").lower() == "true"

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
            "max_slots": OptionsCapitalConfig.MAX_SLOTS,
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
    ENTRY_PCR_MIN = 0.5        # Don't buy PE if PCR too low (already very bullish)
    ENTRY_PCR_MAX = 1.3        # Can buy CE even if slightly bearish (loose upper limit)
    
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
    
    ENABLE_SENTIMENT_FILTER = True            # Global toggle for sentiment checks (errors non-blocking)
    ENABLE_SENTIMENT_EXIT = True             # Enable fade-based exit detection
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
    
    ENABLE_EARLY_EXIT_MOMENTUM = True        # Exit early if momentum reverses post-entry
    EARLY_EXIT_MOMENTUM_THRESHOLD = 10.0     # Exit if price drops >10% from peak (catches 75% of hard SLs)
    # Example: Entry ₹100 → Peak ₹104 → Current ₹93.6 (10% below peak) → EXIT
    # Impact: Saves 69% of losses vs waiting for hard SL (-20%)
    # False positive rate: ~3-5% on winners (acceptable)
    
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
