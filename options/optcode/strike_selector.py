"""
Strike Selection Helper - CE (Call Options) Only

Utility functions for selecting Call option (CE) strikes for options trading.
Integrates with InstrumentManager for real-time strike lookup.

Note: This system deals exclusively with CE (Call) options.
PE (Put) options are not used.
"""

import json
from typing import Optional, Dict, List
from .instrument_manager import get_instrument_manager
from .optlogging import logger

# =============================================================================
# Strike Selection Functions
# =============================================================================

def select_atm_strike(underlying: str, expiry: str,
                      target_delta: float = 0.5) -> Optional[Dict]:
    """
    Select ATM CE (Call) strike
    
    Args:
        underlying: Stock name (e.g., "RELIANCE")
        expiry: Expiry date (e.g., "30DEC2025")
        target_delta: Target delta (0.0-1.0) - NOTE: Uses price-based heuristic currently
    
    Returns:
        Selected strike contract or None
    
    Example:
        strike = select_atm_strike("RELIANCE", "30DEC2025", target_delta=0.6)
        print(f"Selected: {strike['symbol']} at {strike['strike']}")
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        logger.warning(f"STRIKE_SELECT: MANAGER_NOT_LOADED | underlying={underlying}")
        return None
    
    # Get all strikes for this underlying and expiry
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    if not strikes:
        logger.warning(f"STRIKE_SELECT: NO_STRIKES | underlying={underlying} | expiry={expiry}")
        return None
    
    # Filter by CE only (Call options)
    filtered = [s for s in strikes if 'CE' in s.get('symbol', '')]
    
    if not filtered:
        logger.warning(f"STRIKE_SELECT: NO_CE | underlying={underlying} | expiry={expiry}")
        return None
    
    # Sort by strike price and select middle (ATM approximation)
    sorted_strikes = sorted(filtered, key=lambda s: float(s.get('strike', 0)))
    selected = sorted_strikes[len(sorted_strikes) // 2]
    
    logger.info(f"STRIKE_SELECT: ATM_CE | underlying={underlying} | expiry={expiry} | selected={selected['symbol']}")
    
    return selected


def select_strike_by_price(underlying: str, expiry: str, contract_type: str,
                           premium_range: tuple = (500, 2000)) -> Optional[Dict]:
    """
    Select strike within a specific premium price range
    
    Args:
        underlying: Stock name
        expiry: Expiry date
        contract_type: "CE" or "PE"
        premium_range: (min_price, max_price) tuple
    
    Returns:
        Selected strike or None
    
    Example:
        strike = select_strike_by_price("RELIANCE", "30DEC2025", "CE", premium_range=(500, 1500))
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return None
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    if not strikes:
        return None
    
    # Filter by CE only (Call options)
    filtered = [s for s in strikes if 'CE' in s.get('symbol', '')]
    
    if not filtered:
        return None
    
    # Sort by strike price and select middle
    filtered_sorted = sorted(filtered, key=lambda s: float(s.get('strike', 0)))
    
    if filtered_sorted:
        selected = filtered_sorted[len(filtered_sorted) // 2]
        logger.info(f"STRIKE_SELECT: PRICE | underlying={underlying} | range={premium_range} | selected={selected['symbol']}")
        return selected
    
    return None


def select_atm_strike_by_spot(underlying: str, expiry: str,
                              spot_price: Optional[float] = None) -> Optional[Dict]:
    """
    Select ATM CE (Call) strike closest to spot price
    
    Args:
        underlying: Stock name
        expiry: Expiry date
        spot_price: Current spot price (if None, uses generic ATM logic)
    
    Returns:
        ATM CE strike or None
    
    Example:
        atm_call = select_atm_strike_by_spot("RELIANCE", "30DEC2025", spot_price=2850)
        print(f"ATM Strike: {atm_call['strike']}")
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return None
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    if not strikes:
        return None
    
    # Filter by CE only (Call options)
    filtered = [s for s in strikes if 'CE' in s.get('symbol', '')]
    
    if not filtered:
        return None
    
    # Sort by strike price
    filtered_sorted = sorted(filtered, key=lambda s: float(s.get('strike', 0)))
    
    if spot_price is None:
        # If no spot price, use middle strike as ATM
        selected = filtered_sorted[len(filtered_sorted) // 2]
    else:
        # Find CE strike closest to spot price
        selected = min(filtered_sorted, key=lambda s: abs(float(s.get('strike', 0)) - spot_price))
    
    logger.info(f"STRIKE_SELECT: ATM_BY_SPOT | underlying={underlying} | expiry={expiry} | strike={selected['strike']}")
    
    return selected


def list_all_ce_strikes(underlying: str, expiry: str) -> List[Dict]:
    """
    List all available CE (Call) strikes for an underlying
    
    Args:
        underlying: Stock name
        expiry: Expiry date
    
    Returns:
        List of CE strike contracts
    
    Example:
        ce_strikes = list_all_ce_strikes("RELIANCE", "30DEC2025")
        print(f"CE strikes: {[s['strike'] for s in ce_strikes]}")
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return []
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    ce_strikes = [s for s in strikes if "CE" in s.get('symbol', '')]
    ce_strikes_sorted = sorted(ce_strikes, key=lambda s: float(s.get('strike', 0)))
    
    logger.info(f"STRIKE_LIST: underlying={underlying} | expiry={expiry} | ce_count={len(ce_strikes_sorted)}")
    
    return ce_strikes_sorted


def get_strike_token(underlying: str, strike_price: float, expiry: str) -> Optional[str]:
    """
    Get token for a specific CE strike (needed for broker order placement)
    
    Args:
        underlying: Stock name
        strike_price: Strike price
        expiry: Expiry date
    
    Returns:
        Token string or None
    
    Example:
        token = get_strike_token("RELIANCE", 2850, "30DEC2025")
        # Use token for broker.place_order(token, ...)
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return None
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    if not strikes:
        return None
    
    # Find CE strike with exact price match
    for strike in strikes:
        if (float(strike.get('strike', 0)) == strike_price and 
            'CE' in strike.get('symbol', '')):
            token = strike.get('token')
            logger.debug(f"STRIKE_TOKEN: FOUND | underlying={underlying} | strike={strike_price} | token={token}")
            return token
    
    logger.warning(f"STRIKE_TOKEN: NOT_FOUND | underlying={underlying} | strike={strike_price}")
    return None


def instrument_stats() -> Dict:
    """Get instrument manager statistics"""
    mgr = get_instrument_manager()
    return mgr.get_stats()
