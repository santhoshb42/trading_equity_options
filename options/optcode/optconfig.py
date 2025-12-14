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
        """Calculate option contracts (lots) for available capital"""
        if premium <= 0:
            return 0
        # Options are traded in lots, typically 1 lot = multiplier * 1 contract
        contract_cost = premium * lot_size
        max_lots = int(capital / contract_cost)
        return max(1, max_lots)
    
    @classmethod
    def get_available_capital(cls, used_capital: float) -> float:
        """Get available capital after reserves"""
        available = cls.MAX_CAPITAL - cls.RESERVE_CAPITAL - used_capital
        return max(0, available)

# =============================================================================
# Options Trading Configuration
# =============================================================================

class OptionsTradingConfig:
    """Options-specific trading strategy and risk management"""
    
    # Trading mode - PAPER ONLY for now
    TRADING_MODE = "PAPER"  # PAPER mode only - no LIVE trading
    
    # Underlying indexes for options trading (legacy - keep for backward compatibility)
    UNDERLYING_INDEXES = ["BANKNIFTY", "NIFTY", "FINNIFTY"]  # Preferred underlying indexes
    
    # F&O Universe - Complete NSE stock list for deriving strikes
    FO_UNIVERSE = [
        "PEL", "AARTIIND", "ABB", "ABCAPITAL", "ABFRL", "ACC", "ADANIENSOL", "ADANIENT", 
        "ADANIGREEN", "ADANIPORTS", "ALKEM", "AMBER", "AMBUJACEM", "ANGELONE", "APLAPOLLO", 
        "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ASTRAL", "ATGL", "AUBANK", "AUROPHARMA", 
        "AXISBANK", "BAJAJFINSV", "BAJAJ_AUTO", "BAJFINANCE", "BALKRISIND", "BANKBARODA", 
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
        "POLICYBZR", "POLYCAB", "POONAWALLA", "POWERGRID", "POWERINDIA", "PRESTIGE", "RECLTD", 
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
    
    # Risk management - TRIAL MODE: 20% SL with 10% gain trailing
    MAX_LOSS_PER_TRADE = float(os.getenv("OPTIONS_MAX_LOSS_PER_TRADE", "500"))  # Max loss per trade in ₹
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
    
    # Paper trading ALWAYS enabled (no LIVE trading)
    PAPER_TRADING_ENABLED = True  # PAPER mode only - no live trading
    
    # Debug logging
    DEBUG = os.getenv("OPTIONS_DEBUG", "True").lower() == "true"  # Extensive debugging
    
    # Crash recovery
    ENABLE_CRASH_RECOVERY = os.getenv("OPTIONS_CRASH_RECOVERY", "True").lower() == "true"

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
    
    ENABLE_SENTIMENT_FILTER = True           # Global toggle for sentiment checks
    ENABLE_SENTIMENT_EXIT = True             # Enable fade-based exit detection
    LOG_SENTIMENT_CHECKS = True              # Log all sentiment decisions
    ALERT_ON_SENTIMENT_CHANGE = True         # Send alerts when sentiment changes
    
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
