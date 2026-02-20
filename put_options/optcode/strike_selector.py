"""
Strike Selection Helper - PE (Put Options)

Utility functions for selecting Put option (PE) strikes for options trading.
Integrates with InstrumentManager for real-time strike lookup.

Key Difference from CE:
- CE (Call): Select HIGHER strikes (above underlying price) - benefit from upside
- PE (Put): Select LOWER strikes (below underlying price) - benefit from downside

Put Options Strategy:
- When underlying falls, PE options gain value
- We select OTM (out-of-the-money) puts: strikes BELOW current underlying price
- Lower strike = cheaper premium, more leverage, more risk
- ATM selection: picks strike below spot for decent time decay & delta
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
    Select ATM PE (Put) strike
    
    For PE (Put Options):
    - ATM is the strike BELOW the current underlying price
    - We want OTM puts (below current price) for leveraged downside exposure
    - Selecting lower strikes increases leverage and risk
    
    Args:
        underlying: Stock name (e.g., "RELIANCE")
        expiry: Expiry date (e.g., "30DEC2025")
        target_delta: Target delta (0.0-1.0) - Uses price-based heuristic
    
    Returns:
        Selected PE strike contract or None
    
    Example:
        if underlying is at 2850:
        strike = select_atm_strike("RELIANCE", "30DEC2025")
        will select a PE strike around 2800 or 2750 (below spot)
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
    
    # Filter by PE only (Put options)
    filtered = [s for s in strikes if 'PE' in s.get('symbol', '')]
    
    if not filtered:
        logger.warning(f"STRIKE_SELECT: NO_PE | underlying={underlying} | expiry={expiry}")
        return None
    
    # Sort by strike price and select LOWER third for OTM puts
    # This gives us leverage while maintaining decent premium
    sorted_strikes = sorted(filtered, key=lambda s: float(s.get('strike', 0)))
    
    # For PE, select lower strikes (below spot) - using lower third instead of middle
    # This gives us better leverage for downside moves
    selection_index = int(len(sorted_strikes) * 0.35)  # Lower third for OTM
    selected = sorted_strikes[max(0, selection_index)]
    
    logger.info(f"STRIKE_SELECT: ATM_PE | underlying={underlying} | expiry={expiry} | "
                f"selected={selected['symbol']} | strike_price={selected['strike']}")
    
    return selected


def select_strike_by_price(underlying: str, expiry: str, contract_type: str,
                           premium_range: tuple = (500, 2000)) -> Optional[Dict]:
    """
    Select PE strike within a specific premium price range
    
    For PE (Put) options:
    - Filters by PE contracts only
    - Selects LOWER strikes (OTM, below spot) for leveraged downside
    
    Args:
        underlying: Stock name
        expiry: Expiry date
        contract_type: Ignored for PUT bot (always PE)
        premium_range: (min_price, max_price) tuple
    
    Returns:
        Selected PE strike or None
    
    Example:
        strike = select_strike_by_price("RELIANCE", "30DEC2025", "PE", premium_range=(500, 1500))
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return None
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    if not strikes:
        return None
    
    # Filter by PE only (Put options) - for PUT bot, always PE
    filtered = [s for s in strikes if 'PE' in s.get('symbol', '')]
    
    if not filtered:
        return None
    
    # Sort by strike price and select LOWER strikes (OTM for puts)
    filtered_sorted = sorted(filtered, key=lambda s: float(s.get('strike', 0)))
    
    if filtered_sorted:
        # Select lower third for OTM PE puts
        selection_index = int(len(filtered_sorted) * 0.35)
        selected = filtered_sorted[max(0, selection_index)]
        logger.info(f"STRIKE_SELECT: PRICE_PE | underlying={underlying} | range={premium_range} | "
                   f"selected={selected['symbol']} | strike={selected['strike']}")
        return selected
    
    return None


def select_atm_strike_by_spot(underlying: str, expiry: str,
                              spot_price: Optional[float] = None) -> Optional[Dict]:
    """
    Select ATM PE (Put) strike closest to or below spot price
    
    For PE (Put Options):
    - Selects OTM puts: strikes BELOW the current underlying price
    - Benefits from downside moves in the underlying
    
    Args:
        underlying: Stock name
        expiry: Expiry date
        spot_price: Current spot price (if None, uses generic ATM logic)
    
    Returns:
        OTM PE strike below spot price or None
    
    Example:
        if underlying spot is 2850:
        put = select_atm_strike_by_spot("RELIANCE", "30DEC2025", spot_price=2850)
        will select a PE strike around 2750-2800 (below spot)
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return None
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    if not strikes:
        return None
    
    # Filter by PE only (Put options)
    filtered = [s for s in strikes if 'PE' in s.get('symbol', '')]
    
    if not filtered:
        return None
    
    # Sort by strike price
    filtered_sorted = sorted(filtered, key=lambda s: float(s.get('strike', 0)))
    
    if spot_price is None:
        # If no spot price, use lower third (OTM puts)
        selection_index = int(len(filtered_sorted) * 0.35)
        selected = filtered_sorted[max(0, selection_index)]
    else:
        # Find PE strike closest to spot but BELOW it (OTM)
        below_spot = [s for s in filtered_sorted if float(s.get('strike', 0)) < spot_price]
        
        if below_spot:
            # Get the highest strike below spot (closest to ATM from below)
            selected = below_spot[-1]
        else:
            # If no strikes below spot, use the lowest available
            selected = filtered_sorted[0]
    
    logger.info(f"STRIKE_SELECT: ATM_BY_SPOT_PE | underlying={underlying} | expiry={expiry} | "
                f"spot={spot_price} | strike={selected['strike']}")
    
    return selected


def list_all_ce_strikes(underlying: str, expiry: str) -> List[Dict]:
    """
    List all available PE (Put) strikes for an underlying
    
    Args:
        underlying: Stock name
        expiry: Expiry date
    
    Returns:
        List of PE strike contracts sorted by strike price
    
    Example:
        pe_strikes = list_all_ce_strikes("RELIANCE", "30DEC2025")
        print(f"PE strikes available: {[s['strike'] for s in pe_strikes]}")
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return []
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    pe_strikes = [s for s in strikes if "PE" in s.get('symbol', '')]
    pe_strikes_sorted = sorted(pe_strikes, key=lambda s: float(s.get('strike', 0)))
    
    logger.info(f"STRIKE_LIST_PE: underlying={underlying} | expiry={expiry} | pe_count={len(pe_strikes_sorted)}")
    
    return pe_strikes_sorted


def get_strike_token(underlying: str, strike_price: float, expiry: str) -> Optional[str]:
    """
    Get token for a specific PE strike (needed for broker order placement)
    
    Args:
        underlying: Stock name
        strike_price: Strike price
        expiry: Expiry date
    
    Returns:
        Token string or None
    
    Example:
        token = get_strike_token("RELIANCE", 2750, "30DEC2025")
        # Use token for broker.place_order(token, ...) on PE put
    """
    mgr = get_instrument_manager()
    
    if not mgr.is_loaded:
        return None
    
    strikes = mgr.get_strikes_for_underlying_and_expiry(underlying, expiry)
    
    if not strikes:
        return None
    
    # Find PE strike with exact price match
    for strike in strikes:
        if (float(strike.get('strike', 0)) == strike_price and 
            'PE' in strike.get('symbol', '')):
            token = strike.get('token')
            logger.debug(f"STRIKE_TOKEN_PE: FOUND | underlying={underlying} | strike={strike_price} | "
                        f"token={token}")
            return token
    
    logger.warning(f"STRIKE_TOKEN_PE: NOT_FOUND | underlying={underlying} | strike={strike_price}")
    return None


def instrument_stats() -> Dict:
    """Get instrument manager statistics"""
    mgr = get_instrument_manager()
    return mgr.get_stats()
