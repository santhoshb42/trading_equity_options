"""
Configuration Module - Equity Trading Bot

All AngelOne parameters, budget settings, and configuration defines.
This file centralizes all bot configuration including:
- AngelOne API parameters
- Capital and budget management
- Trading parameters
- Risk management settings
"""

import os
from typing import Dict, Any
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

# =============================================================================
# AngelOne API Configuration
# =============================================================================

class AngelOneConfig:
    """AngelOne SmartAPI configuration"""
    
    # API Credentials (from .env)
    API_KEY = os.getenv("ANGEL_API_KEY", "")
    CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "")
    PASSWORD = os.getenv("ANGEL_PASSWORD", "")
    TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")
    
    # API URLs
    BASE_URL = "https://apiconnect.angelbroking.com"
    LOGIN_URL = f"{BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword"
    
    # Session Management
    SESSION_EXPIRY_HOURS = 28  # AngelOne JWT expires in 28 hours
    SESSION_REFRESH_TIME = "09:00"  # Refresh daily at 9 AM IST
    
    # Rate Limiting (Optimized for Alert Bursts)
    # Actual limits: 10 req/sec, 200 req/min
    # Updated to better handle alert bursts while staying safe
    REQUESTS_PER_SECOND = 8   # AngelOne limit: 10 req/s, using 8 for safety margin
    REQUESTS_PER_MINUTE = 180  # AngelOne limit: 200 req/min, using 180 for safety margin
    
    # Rate limiter monitoring
    RATE_LIMIT_WARNING_THRESHOLD = 0.8  # Warn when 80% of limits used
    RATE_LIMIT_CRITICAL_THRESHOLD = 0.9  # Critical when 90% of limits used
    
    # Order Configuration
    PRODUCT_TYPE = "INTRADAY"  # Intraday trading (Angel One API uses "INTRADAY" not "MIS")
    ORDER_TYPE = "MARKET"  # Market orders for quick execution
    DURATION = "DAY"  # Day orders
    
    # Exchange
    EXCHANGE = "NSE"
    
    # Margin Percentage (20% for MIS)
    MARGIN_PERCENTAGE = 0.20

# =============================================================================
# Capital and Budget Management
# =============================================================================

class CapitalConfig:
    """Capital and budget management settings"""
    
    # Total available capital
    MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", "100000"))  # ₹1,00,000
    
    # Capital per trade (for legacy/test compatibility)
    CAP_PER_TRADE = float(os.getenv("CAP_PER_TRADE", "2000"))  # ₹2,000 per trade (test default)
    BUDGET_PER_TRADE = CAP_PER_TRADE  # Alias for test cases
    
    # Maximum concurrent positions (slots)
    MAX_SLOTS = int(os.getenv("MAX_SLOTS", "5"))  # Max 5 positions
    MAX_SIMULTANEOUS_POSITIONS = MAX_SLOTS  # Alias for test cases
    
    # Reserve capital (emergency buffer)
    RESERVE_CAPITAL = float(os.getenv("RESERVE_CAPITAL", "10000"))  # ₹10,000 reserve
    
    # Commission and charges per trade
    BROKERAGE_PER_TRADE = 20.0  # ₹20 flat brokerage
    STT_PERCENTAGE = 0.001  # 0.1% STT on sell side
    TRANSACTION_CHARGES = 0.0000345  # NSE transaction charges
    GST_PERCENTAGE = 0.18  # 18% GST on brokerage
    SEBI_CHARGES = 0.000001  # SEBI charges
    STAMP_DUTY = 0.00003  # Stamp duty on buy side
    
    @classmethod
    def calculate_total_charges(cls, trade_value: float) -> float:
        """Calculate total charges for a trade"""
        brokerage = cls.BROKERAGE_PER_TRADE
        stt = trade_value * cls.STT_PERCENTAGE
        transaction_charges = trade_value * cls.TRANSACTION_CHARGES
        gst = brokerage * cls.GST_PERCENTAGE
        sebi_charges = trade_value * cls.SEBI_CHARGES
        stamp_duty = trade_value * cls.STAMP_DUTY
        
        total_charges = brokerage + stt + transaction_charges + gst + sebi_charges + stamp_duty
        return round(total_charges, 2)
    
    @classmethod
    def calculate_quantity_for_capital(cls, price: float, capital: float) -> int:
        """
        Calculate number of shares for given capital (including charges)
        Since AngelOne MIS has 20% margin, we can use 5x leverage
        
        Formula:
        - With ₹2000 capital and 20% margin, max trade value = ₹2000 / 0.20 = ₹10000
        - Quantity = ₹10000 / price
        - Margin needed = quantity × price × 0.20
        - Total cost = margin + charges (must be ≤ capital)
        """
        margin_percentage = AngelOneConfig.MARGIN_PERCENTAGE
        
        # Maximum trade value with leverage
        max_trade_value = capital / margin_percentage
        
        # Calculate maximum quantity
        max_quantity = int(max_trade_value / price)
        
        # Verify we have enough capital for margin + charges
        actual_trade_value = max_quantity * price
        margin_required = actual_trade_value * margin_percentage
        estimated_charges = cls.calculate_total_charges(actual_trade_value)
        total_cost = margin_required + estimated_charges
        
        # If total cost exceeds capital, reduce quantity
        if total_cost > capital and max_quantity > 1:
            # Reduce quantity until we fit within budget
            while total_cost > capital and max_quantity > 1:
                max_quantity -= 1
                actual_trade_value = max_quantity * price
                margin_required = actual_trade_value * margin_percentage
                estimated_charges = cls.calculate_total_charges(actual_trade_value)
                total_cost = margin_required + estimated_charges
        
        return max(1, max_quantity)  # Minimum 1 share

# =============================================================================
# Trading Parameters
# =============================================================================

class TradingConfig:
    """Trading strategy and risk management parameters"""
    
    # Trading mode
    TRADING_MODE = os.getenv("TRADING_MODE", "LIVE")  # PAPER or LIVE
    
    # Capital per trade
    CAPITAL_PER_TRADE = float(os.getenv("CAPITAL_PER_TRADE", "2000"))  # ₹2,000 per trade (test default)
    
    # Maximum positions
    MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))  # Maximum 5 concurrent positions
    MAX_SIMULTANEOUS_POSITIONS = MAX_POSITIONS  # Alias for test cases
    
    # Symbol processing
    SYMBOL_SUFFIX = "-EQ"  # Append to symbols from TradingView
    
    # Stop Loss and Risk Management
    DEFAULT_SL_PERCENTAGE = float(os.getenv("DEFAULT_SL_PERCENTAGE", "0.5"))  # 0.5% default SL (tighter stops for profitability)
    
    # Trailing Stop Loss
    TRAIL_SL_ENABLED = os.getenv("TRAIL_SL_ENABLED", "True").lower() == "true"
    TRAIL_SL_PERCENTAGE = float(os.getenv("TRAIL_SL_PERCENTAGE", "0.5"))  # 0.5% trail (as requested)
    TRAIL_TRIGGER_PERCENTAGE = float(os.getenv("TRAIL_TRIGGER_PERCENTAGE", "0.5"))  # Trigger at 0.5% profit (immediate)
    
    # Target (not used as per requirement - we only use trailing SL)
    USE_TARGET = False
    
    # Monitoring frequency
    MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL", "20"))  # Set to 20 seconds (increased from 15s) to reduce API load and prevent AG8001 rate limit errors
    
    # Adaptive monitoring based on rate limits
    MONITOR_INTERVAL_FAST = 15      # When rate limits are healthy (increased from 10s)
    MONITOR_INTERVAL_NORMAL = 20    # Normal monitoring (increased from 15s)
    MONITOR_INTERVAL_SLOW = 45     # When rate limits are stressed (increased from 30s)
    
    # 🆕 BUCKETED LTP CHECKING: Divide positions into buckets to reduce API calls
    # With bucket_size=5 and 20 positions: 4 buckets × 5 calls/bucket = 5 calls/cycle
    # Instead of 20 calls/cycle = 75% reduction in API calls!
    LTP_BUCKET_SIZE = int(os.getenv("LTP_BUCKET_SIZE", "3"))  # Positions per bucket (reduced from 5 to prioritize order placement)

    # Enable the async priority API queue (routes API calls through priority queue)
    # Set to False to keep legacy direct-call behaviour
    ENABLE_API_QUEUE = os.getenv("ENABLE_API_QUEUE", "True").lower() == "true"
    
    # Order confirmation timeout
    ORDER_CONFIRMATION_TIMEOUT = int(os.getenv("ORDER_TIMEOUT", "30"))  # 30 seconds
    
    # Market hours (IST)
    MARKET_OPEN_TIME = "09:15"
    MARKET_CLOSE_TIME = "15:30"
    AUTO_SQUARE_OFF_TIME = "15:20"  # Auto square-off 10 minutes before close

# =============================================================================
# Webhook Configuration
# =============================================================================

class WebhookConfig:
    """TradingView webhook configuration"""
    
    # Webhook server settings
    WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))  # Port 8080 - receives from webhook router on port 80
    
    # Security
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Optional secret for webhook validation
    
    # Expected alert format from TradingView
    EXPECTED_FIELDS = ["symbol", "action", "price"]
    
    # Valid actions
    VALID_ACTIONS = ["BUY", "SELL", "EXIT"]

# =============================================================================
# Development and Testing
# =============================================================================

class DevConfig:
    """Development and testing configuration"""
    
    # Testing mode (for unit tests)
    TESTING_MODE = os.getenv("TESTING_MODE", "False").lower() == "true"
    
    # Debug mode
    DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() == "true"
    
    @staticmethod
    def is_paper_trading():
        """Check if paper trading is enabled - evaluates dynamically"""
        # Default to LIVE mode for production. Only use PAPER if explicitly set.
        trading_mode = os.getenv("TRADING_MODE", "LIVE").upper()
        return trading_mode == "PAPER"
    
    # Mock prices for testing
    MOCK_PRICES = {
        "RELIANCE-EQ": 2450.50,
        "TCS-EQ": 3650.75,
        "INFY-EQ": 1890.25,
        "HDFC-EQ": 1650.30,
        "ICICIBANK-EQ": 950.60
    }

# =============================================================================
# Validation Functions
# =============================================================================

def validate_config() -> tuple[bool, list[str]]:
    """
    Validate all configuration parameters
    
    Returns:
        tuple: (is_valid, list_of_errors)
    """
    errors = []
    
    # Validate AngelOne credentials
    if not AngelOneConfig.API_KEY:
        errors.append("ANGEL_API_KEY not set in environment")
    
    if not AngelOneConfig.CLIENT_CODE:
        errors.append("ANGEL_CLIENT_CODE not set in environment")
    
    if not AngelOneConfig.PASSWORD:
        errors.append("ANGEL_PASSWORD not set in environment")
    
    # Validate capital configuration
    if CapitalConfig.MAX_CAPITAL <= 0:
        errors.append("MAX_CAPITAL must be positive")
    
    if CapitalConfig.CAP_PER_TRADE <= 0:
        errors.append("CAP_PER_TRADE must be positive")
    
    if CapitalConfig.CAP_PER_TRADE > CapitalConfig.MAX_CAPITAL:
        errors.append("CAP_PER_TRADE cannot exceed MAX_CAPITAL")
    
    if CapitalConfig.MAX_SLOTS <= 0:
        errors.append("MAX_SLOTS must be positive")
    
    # Validate trading parameters
    if TradingConfig.DEFAULT_SL_PERCENTAGE <= 0 or TradingConfig.DEFAULT_SL_PERCENTAGE > 10:
        errors.append("DEFAULT_SL_PERCENTAGE must be between 0 and 10")
    
    if TradingConfig.TRADING_MODE not in ["PAPER", "LIVE"]:
        errors.append("TRADING_MODE must be either 'PAPER' or 'LIVE'")
    
    # Validate paths
    if not BASE_DIR.exists():
        errors.append(f"Base directory does not exist: {BASE_DIR}")
    
    return len(errors) == 0, errors

# =============================================================================
# Configuration Summary
# =============================================================================

def get_config_summary() -> Dict[str, Any]:
    """Get a summary of current configuration"""
    return {
        "trading_mode": TradingConfig.TRADING_MODE,
        "max_capital": CapitalConfig.MAX_CAPITAL,
        "cap_per_trade": CapitalConfig.CAP_PER_TRADE,
        "max_slots": CapitalConfig.MAX_SLOTS,
        "default_sl_percentage": TradingConfig.DEFAULT_SL_PERCENTAGE,
        "trail_sl_enabled": TradingConfig.TRAIL_SL_ENABLED,
        "monitor_interval": TradingConfig.MONITOR_INTERVAL_SECONDS,
        "webhook_port": WebhookConfig.WEBHOOK_PORT,
        "margin_percentage": AngelOneConfig.MARGIN_PERCENTAGE,
        "paper_trading": DevConfig.is_paper_trading(),
    }

# =============================================================================
# Market Hours Utility Functions
# =============================================================================

def is_market_open() -> bool:
    """
    Check if NSE market is currently open (9:15 AM to 3:30 PM IST).
    
    Returns:
        True if market is open, False otherwise
    """
    from datetime import datetime
    import pytz
    
    try:
        # Get current time in IST
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        # Parse market open and close times
        open_time = datetime.strptime(TradingConfig.MARKET_OPEN_TIME, "%H:%M").time()
        close_time = datetime.strptime(TradingConfig.MARKET_CLOSE_TIME, "%H:%M").time()
        
        current_time = now.time()
        
        # Check if market is open (and it's a weekday)
        is_weekday = now.weekday() < 5  # Monday=0, Friday=4
        is_within_hours = open_time <= current_time <= close_time
        
        return is_weekday and is_within_hours
    
    except Exception as e:
        print(f"Error checking market hours: {e}")
        # Default to considering market open if we can't determine
        return True

def get_market_status() -> Dict[str, Any]:
    """
    Get detailed market status information.
    
    Returns:
        Dictionary with market status details
    """
    from datetime import datetime
    import pytz
    
    try:
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        open_time = datetime.strptime(TradingConfig.MARKET_OPEN_TIME, "%H:%M").time()
        close_time = datetime.strptime(TradingConfig.MARKET_CLOSE_TIME, "%H:%M").time()
        
        current_time = now.time()
        is_open = is_market_open()
        
        # Calculate hours elapsed and remaining
        from datetime import datetime as dt
        open_dt = dt.combine(now.date(), open_time)
        close_dt = dt.combine(now.date(), close_time)
        now_dt = dt.combine(now.date(), current_time)
        
        hours_elapsed = (now_dt - open_dt).total_seconds() / 3600 if now_dt >= open_dt else 0
        hours_remaining = (close_dt - now_dt).total_seconds() / 3600 if now_dt <= close_dt else 0
        
        return {
            'is_open': is_open,
            'current_time': current_time.isoformat(),
            'market_open': open_time.isoformat(),
            'market_close': close_time.isoformat(),
            'hours_elapsed': round(hours_elapsed, 2),
            'hours_remaining': round(hours_remaining, 2),
            'day_of_week': now.strftime('%A')
        }
    
    except Exception as e:
        return {
            'is_open': True,
            'error': str(e)
        }

# =============================================================================
# Testing
# =============================================================================

if __name__ == "__main__":
    """Test configuration validation"""
    print("=== Equity Trading Bot Configuration ===")
    print()
    
    # Show configuration summary
    config = get_config_summary()
    print("Current Configuration:")
    for key, value in config.items():
        print(f"  {key}: {value}")
    
    print()
    
    # Validate configuration
    is_valid, errors = validate_config()
    
    if is_valid:
        print("✅ Configuration is valid")
    else:
        print("❌ Configuration errors:")
        for error in errors:
            print(f"  - {error}")
    
    print()
    
    # Test capital calculation
    print("Capital Calculation Test:")
    test_price = 2450.50
    test_capital = CapitalConfig.CAP_PER_TRADE
    
    quantity = CapitalConfig.calculate_quantity_for_capital(test_price, test_capital)
    trade_value = quantity * test_price
    charges = CapitalConfig.calculate_total_charges(trade_value)
    margin_required = trade_value * AngelOneConfig.MARGIN_PERCENTAGE
    
    print(f"  Price: ₹{test_price}")
    print(f"  Capital: ₹{test_capital}")
    print(f"  Calculated quantity: {quantity}")
    print(f"  Trade value: ₹{trade_value:.2f}")
    print(f"  Margin required: ₹{margin_required:.2f}")
    print(f"  Estimated charges: ₹{charges:.2f}")
    print(f"  Total cost: ₹{margin_required + charges:.2f}")