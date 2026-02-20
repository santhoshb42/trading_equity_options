"""
Market Condition Detector for Adaptive TRIAL_SL Threshold

Analyzes current market conditions to dynamically switch between:
- 5% TRIAL_SL threshold: Weak/choppy markets (protect capital)
- 10% TRIAL_SL threshold: Strong trending markets (maximize gains)
"""

from datetime import datetime, time
from typing import Dict, Tuple
from .optlogging import logger

class MarketConditionDetector:
    """
    Determines current market strength and recommends TRIAL_SL threshold
    """
    
    def __init__(self):
        self.nifty_open_price = None
        self.nifty_prev_close = None
        self.nifty_session_open = None  # Market session open price (9:15 AM)
        self.last_market_check = None
        self.current_threshold = 10.0  # Default to 10%
        self.market_state = "UNKNOWN"
        self.nifty_open_recorded_at = None
        
    def update_market_data(self, nifty_ltp: float, nifty_open: float = None, nifty_prev_close: float = None):
        """
        Update market data for condition analysis
        
        Args:
            nifty_ltp: Current Nifty price
            nifty_open: Today's open (optional)
            nifty_prev_close: Previous day close (optional)
        """
        if nifty_open:
            self.nifty_open_price = nifty_open
        if nifty_prev_close:
            self.nifty_prev_close = nifty_prev_close
            
        self.last_market_check = datetime.now()
        
    def get_trial_sl_threshold(self, nifty_ltp: float = None, 
                               current_time: datetime = None) -> Tuple[float, str]:
        """
        Determine optimal TRIAL_SL threshold based on market conditions
        
        Returns:
            (threshold_percent, reason)
        """
        if current_time is None:
            current_time = datetime.now()
            
        # If nifty_ltp not provided, try to fetch it dynamically
        if nifty_ltp is None:
            try:
                # Import broker here to avoid circular imports
                from .angelone_options import get_options_broker
                broker = get_options_broker()
                nifty_data = broker.get_market_data("NIFTY", exchange="NSE")
                if nifty_data:
                    nifty_ltp = nifty_data.get('ltp')
                    nifty_open = nifty_data.get('open')
                    if nifty_ltp:
                        # If we have open price, use it
                        if nifty_open:
                            self.update_market_data(nifty_ltp=nifty_ltp, nifty_open=nifty_open)
                            logger.debug(f"MARKET_THRESHOLD: Dynamically fetched Nifty | LTP={nifty_ltp:.2f} | Open={nifty_open:.2f}")
                        # Otherwise, set session open on first fetch of the day
                        elif not self.nifty_session_open:
                            self.nifty_session_open = nifty_ltp
                            self.nifty_open_recorded_at = current_time
                            logger.info(f"MARKET_THRESHOLD: Recorded session open at {current_time.time()} | Nifty={nifty_ltp:.2f}")
            except Exception as e:
                logger.debug(f"MARKET_THRESHOLD: Could not dynamically fetch Nifty | {str(e)}")
        
        # Determine reference open price (prefer actual open, fallback to session open)
        reference_open = self.nifty_open_price or self.nifty_session_open
        
        # Default values if no market data - use 5% for conservative protection in low markets
        if nifty_ltp is None or reference_open is None:
            return 5.0, "DEFAULT_CONSERVATIVE (no market data - low market protection)"
        
        # Calculate market momentum from reference open
        change_from_open = ((nifty_ltp - reference_open) / reference_open * 100)
        
        # Time of day factor (more aggressive in morning, conservative in afternoon)
        market_time = current_time.time()
        is_morning = time(9, 15) <= market_time < time(11, 30)
        is_afternoon = time(13, 0) <= market_time < time(15, 0)
        
        # DECISION LOGIC
        # Strong uptrend: Use 10% threshold to capture big moves
        if change_from_open >= 0.8:
            threshold = 10.0
            state = "STRONG_UPTREND"
            reason = f"Nifty +{change_from_open:.2f}% (strong momentum)"
            
        # Moderate uptrend: Use 10% in morning, 5% in afternoon
        elif 0.3 <= change_from_open < 0.8:
            if is_morning:
                threshold = 10.0
                state = "MODERATE_UP_MORNING"
                reason = f"Nifty +{change_from_open:.2f}% (morning session)"
            else:
                threshold = 5.0
                state = "MODERATE_UP_AFTERNOON"
                reason = f"Nifty +{change_from_open:.2f}% (afternoon - tighten)"
                
        # Flat/choppy: Always use 5% for protection
        elif -0.3 <= change_from_open < 0.3:
            threshold = 5.0
            state = "FLAT_CHOPPY"
            reason = f"Nifty {change_from_open:+.2f}% (choppy/flat)"
            
        # Weak/down: Definitely use 5% for capital protection
        else:
            threshold = 5.0
            state = "WEAK_DOWNTREND"
            reason = f"Nifty {change_from_open:+.2f}% (weak market)"
        
        # Log if threshold changed
        if threshold != self.current_threshold:
            logger.info(f"TRIAL_SL_THRESHOLD_CHANGE: {self.current_threshold}% → {threshold}% | "
                       f"Market: {state} | {reason}")
        
        self.current_threshold = threshold
        self.market_state = state
        
        return threshold, reason
    
    def get_current_state(self) -> Dict:
        """Get current market state summary"""
        return {
            "threshold": self.current_threshold,
            "market_state": self.market_state,
            "nifty_open": self.nifty_open_price,
            "last_check": self.last_market_check.isoformat() if self.last_market_check else None
        }


# Singleton instance
_market_detector = None

def get_market_condition_detector() -> MarketConditionDetector:
    """Get singleton market condition detector"""
    global _market_detector
    if _market_detector is None:
        _market_detector = MarketConditionDetector()
    return _market_detector
