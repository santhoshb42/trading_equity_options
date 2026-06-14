"""
Correlation Risk Management Module (Week 3 P3.1)
Detects correlated losses and prevents opening new positions in correlated stocks
Prevents cascading losses from market-wide downturns or sector-wide drops
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set

# Safe imports with fallbacks
try:
    from eqcode.bot_logging import log_event
except ImportError:
    def log_event(event_type: str, message: str, **kwargs):
        print(f"[{event_type}] {message}")


class CorrelationAnalyzer:
    """
    Analyzes correlation between open positions and detects correlated losses
    Prevents trading correlated stocks to avoid cascading losses
    """
    
    # Stock correlations (simplified - can be enhanced with real correlation data)
    # Format: {stock: [correlated_stocks, ...]}
    KNOWN_CORRELATIONS = {
        # Banking stocks (high correlation)
        'HDFC': ['ICICI', 'AXIS', 'INDIABULLS', 'KOTAK'],
        'ICICI': ['HDFC', 'AXIS', 'INDIABULLS', 'KOTAK'],
        'AXIS': ['HDFC', 'ICICI', 'INDIABULLS', 'KOTAK'],
        'KOTAK': ['HDFC', 'ICICI', 'AXIS', 'INDIABULLS'],
        
        # IT stocks (high correlation)
        'TCS': ['INFY', 'WIPRO', 'TECHM', 'LT'],
        'INFY': ['TCS', 'WIPRO', 'TECHM', 'LT'],
        'WIPRO': ['TCS', 'INFY', 'TECHM', 'LT'],
        
        # Auto stocks (high correlation)
        'MARUTI': ['BAJAJANTO', 'EICHER', 'TATAMOTORS'],
        'TATAMOTORS': ['MARUTI', 'BAJAJANTO', 'EICHER'],
        'EICHER': ['MARUTI', 'TATAMOTORS', 'BAJAJANTO'],
        
        # Pharma stocks (high correlation)
        'SUNPHARMA': ['CIPLA', 'DRREDDY', 'LUPIN', 'DIVAPHARMA'],
        'CIPLA': ['SUNPHARMA', 'DRREDDY', 'LUPIN', 'DIVAPHARMA'],
        
        # Energy stocks (high correlation)
        'RELIANCE': ['ONGC', 'GAIL'],
        'ONGC': ['RELIANCE', 'GAIL'],
        'GAIL': ['RELIANCE', 'ONGC'],
        
        # Infra stocks (high correlation)
        'LT': ['DLF', 'ULTRAMARINE'],
        
        # FMCG stocks (medium correlation)
        'ITC': ['BRITANNIA', 'NESTLEIND'],
        'BRITANNIA': ['ITC', 'NESTLEIND'],
    }
    
    def __init__(self, correlation_threshold: float = 0.7, 
                 loss_window_minutes: int = 60,
                 max_correlated_positions: int = 2):
        """
        Initialize correlation analyzer
        
        Args:
            correlation_threshold: Correlation strength threshold (0.0-1.0)
            loss_window_minutes: Time window to look back for losses
            max_correlated_positions: Max positions allowed in correlated group
        """
        self.correlation_threshold = correlation_threshold
        self.loss_window_minutes = loss_window_minutes
        self.max_correlated_positions = max_correlated_positions
        
        # Position tracking with entry details
        self.positions: Dict[str, Dict] = {}
        
        # Trade history for correlation analysis
        self.trade_history: List[Dict] = []
        
        # Correlation tracking
        self.detected_correlations: Dict[str, Set[str]] = {}
        
        # Statistics
        self.correlation_blocks = 0
        self.correlated_losses_avoided = 0
        
        log_event('CORRELATION_INIT', f'CorrelationAnalyzer initialized (threshold: {correlation_threshold})')
    
    def add_position(self, symbol: str, entry_price: float, entry_time: datetime,
                    quantity: int, is_long: bool = True) -> None:
        """
        Add a position to track
        
        Args:
            symbol: Stock symbol
            entry_price: Entry price
            entry_time: Entry time
            quantity: Position size
            is_long: True for BUY, False for SELL
        """
        self.positions[symbol] = {
            'entry_price': entry_price,
            'entry_time': entry_time,
            'quantity': quantity,
            'current_price': entry_price,
            'is_long': is_long,
            'profit_percent': 0.0,
            'is_losing': False,
            'last_update': entry_time
        }
        log_event('CORRELATION_POSITION_ADDED', f'Tracking {symbol} @ {entry_price}', qty=quantity)
    
    def update_position_price(self, symbol: str, current_price: float, update_time: datetime) -> None:
        """
        Update position with current market price
        
        Args:
            symbol: Stock symbol
            current_price: Current market price
            update_time: Update time
        """
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        entry_price = pos['entry_price']
        
        # Calculate profit/loss
        if pos['is_long']:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            profit_pct = ((entry_price - current_price) / entry_price) * 100
        
        pos['current_price'] = current_price
        pos['profit_percent'] = profit_pct
        pos['is_losing'] = profit_pct < -0.5  # Losing if down more than 0.5%
        pos['last_update'] = update_time
    
    def get_correlated_stocks(self, symbol: str) -> Set[str]:
        """
        Get stocks correlated with given symbol
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Set of correlated symbols
        """
        return set(self.KNOWN_CORRELATIONS.get(symbol, []))
    
    def detect_correlated_losses(self) -> Dict[str, List[str]]:
        """
        Detect groups of stocks with correlated losses
        
        Returns:
            Dictionary mapping losing stocks to their correlated losing stocks
        """
        correlated_losses = {}
        
        # Get all losing positions
        losing_stocks = {sym: pos for sym, pos in self.positions.items() if pos['is_losing']}
        
        if not losing_stocks:
            return correlated_losses
        
        # For each losing stock, find correlated ones also losing
        for symbol, pos in losing_stocks.items():
            correlated = self.get_correlated_stocks(symbol)
            losing_correlated = [s for s in correlated if s in losing_stocks]
            
            if losing_correlated:
                correlated_losses[symbol] = losing_correlated
                log_event('CORRELATION_LOSSES_DETECTED', 
                         f'{symbol} has {len(losing_correlated)} correlated losses: {losing_correlated}')
        
        return correlated_losses
    
    def get_correlation_risk_score(self, symbol: str) -> float:
        """
        Calculate correlation risk score for a potential new position
        
        Args:
            symbol: Stock symbol to evaluate
            
        Returns:
            Risk score (0.0 = safe, 1.0 = high risk)
        """
        if not self.positions:
            return 0.0
        
        correlated = self.get_correlated_stocks(symbol)
        if not correlated:
            return 0.0
        
        # Count how many correlated stocks we have open
        open_correlated = [s for s in correlated if s in self.positions]
        if not open_correlated:
            return 0.0
        
        # Count how many are losing
        losing_correlated = [s for s in open_correlated if self.positions[s]['is_losing']]
        
        # Risk score: increases with number of losing correlated positions
        # Formula: (losing_count / open_count) * (open_count / max_allowed)
        if not open_correlated:
            return 0.0
        
        loss_ratio = len(losing_correlated) / len(open_correlated)
        position_ratio = len(open_correlated) / self.max_correlated_positions
        
        risk_score = loss_ratio * position_ratio
        return min(risk_score, 1.0)
    
    def should_block_position(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Determine if position should be blocked due to correlation risk
        
        Args:
            symbol: Stock symbol
            
        Returns:
            (should_block, reason)
        """
        correlated = self.get_correlated_stocks(symbol)
        if not correlated:
            return False, None
        
        # Block if too many correlated positions already open
        open_correlated = [s for s in correlated if s in self.positions]
        if len(open_correlated) >= self.max_correlated_positions:
            reason = f'Already have {len(open_correlated)} correlated positions'
            self.correlation_blocks += 1
            log_event('CORRELATION_BLOCK', f'Blocked {symbol}: {reason}', 
                     correlated_open=open_correlated)
            return True, reason
        
        # Block if multiple correlated losses detected
        correlated_losses = self.detect_correlated_losses()
        losing_in_group = sum(1 for s in open_correlated if self.positions[s]['is_losing'])
        
        if losing_in_group >= 2:
            reason = f'{losing_in_group} correlated losses already open (cascade protection)'
            self.correlation_blocks += 1
            log_event('CORRELATION_BLOCK', f'Blocked {symbol}: {reason}', 
                     losing_positions=losing_in_group)
            return True, reason
        
        # Risk score check
        risk_score = self.get_correlation_risk_score(symbol)
        if risk_score > 0.7:
            reason = f'High correlation risk ({risk_score:.2f})'
            log_event('CORRELATION_WARNING', f'High risk for {symbol}: {reason}')
            # Don't block, just warn (risk score is probabilistic)
        
        return False, None
    
    def close_position(self, symbol: str) -> None:
        """
        Close a position and record for analytics
        
        Args:
            symbol: Stock symbol
        """
        if symbol in self.positions:
            pos = self.positions[symbol]
            
            # Record in history
            self.trade_history.append({
                'symbol': symbol,
                'entry_price': pos['entry_price'],
                'exit_price': pos['current_price'],
                'profit_percent': pos['profit_percent'],
                'entry_time': pos['entry_time'],
                'exit_time': pos['last_update'],
                'is_long': pos['is_long']
            })
            
            del self.positions[symbol]
            log_event('CORRELATION_POSITION_CLOSED', f'Closed {symbol}')
    
    def get_correlation_network(self) -> Dict[str, List[str]]:
        """
        Get current correlation network of open positions
        
        Returns:
            Dictionary showing connected positions
        """
        network = {}
        
        for symbol in self.positions.keys():
            correlated = self.get_correlated_stocks(symbol)
            open_correlated = [s for s in correlated if s in self.positions]
            if open_correlated:
                network[symbol] = open_correlated
        
        return network
    
    def get_sector_exposure(self) -> Dict[str, List[str]]:
        """
        Get current sector exposure (implied from correlations)
        
        Returns:
            Dictionary mapping sectors to open positions
        """
        # Simple sector mapping based on correlations
        sector_map = {}
        
        sectors = {
            'BANKING': ['HDFC', 'ICICI', 'AXIS', 'KOTAK', 'INDIABULLS'],
            'IT': ['TCS', 'INFY', 'WIPRO', 'TECHM', 'LT'],
            'AUTO': ['MARUTI', 'TATAMOTORS', 'EICHER', 'BAJAJANTO'],
            'PHARMA': ['SUNPHARMA', 'CIPLA', 'DRREDDY', 'LUPIN', 'DIVAPHARMA'],
            'ENERGY': ['RELIANCE', 'ONGC', 'GAIL'],
            'INFRA': ['LT', 'DLF', 'ULTRAMARINE'],
            'FMCG': ['ITC', 'BRITANNIA', 'NESTLEIND']
        }
        
        for sector, stocks in sectors.items():
            open_in_sector = [s for s in stocks if s in self.positions]
            if open_in_sector:
                sector_map[sector] = open_in_sector
        
        return sector_map
    
    def get_correlation_statistics(self) -> Dict:
        """
        Get correlation analysis statistics
        
        Returns:
            Dictionary with statistics
        """
        correlated_network = self.get_correlation_network()
        sector_exposure = self.get_sector_exposure()
        correlated_losses = self.detect_correlated_losses()
        
        return {
            'total_positions': len(self.positions),
            'correlation_blocks': self.correlation_blocks,
            'correlated_losses_avoided': self.correlated_losses_avoided,
            'open_correlation_groups': len(correlated_network),
            'sector_concentration': len(sector_exposure),
            'max_sector_positions': max([len(v) for v in sector_exposure.values()], default=0),
            'detected_correlated_losses': correlated_losses,
            'correlation_network': correlated_network,
            'sector_exposure': sector_exposure
        }
    
    def reset_statistics(self) -> None:
        """Reset statistics"""
        self.correlation_blocks = 0
        self.correlated_losses_avoided = 0
        log_event('CORRELATION_RESET', 'Correlation statistics reset')


# Global instance
correlation_analyzer = CorrelationAnalyzer()


# Convenience wrapper functions
def add_position(symbol: str, entry_price: float, entry_time: datetime,
                quantity: int, is_long: bool = True) -> None:
    """Add position to correlation analyzer"""
    try:
        correlation_analyzer.add_position(symbol, entry_price, entry_time, quantity, is_long)
    except Exception as e:
        log_event('CORRELATION_ERROR', f'Error adding position: {str(e)}')


def update_position(symbol: str, current_price: float, update_time: datetime) -> None:
    """Update position price"""
    try:
        correlation_analyzer.update_position_price(symbol, current_price, update_time)
    except Exception as e:
        log_event('CORRELATION_ERROR', f'Error updating position: {str(e)}')


def check_correlation_block(symbol: str) -> Tuple[bool, Optional[str]]:
    """Check if position should be blocked due to correlation"""
    try:
        return correlation_analyzer.should_block_position(symbol)
    except Exception as e:
        log_event('CORRELATION_ERROR', f'Error checking block: {str(e)}')
        return False, None


def get_correlation_risk(symbol: str) -> float:
    """Get correlation risk score (0.0 = safe, 1.0 = high risk)"""
    try:
        return correlation_analyzer.get_correlation_risk_score(symbol)
    except Exception as e:
        log_event('CORRELATION_ERROR', f'Error getting risk: {str(e)}')
        return 0.0


def close_position(symbol: str) -> None:
    """Close position in analyzer"""
    try:
        correlation_analyzer.close_position(symbol)
    except Exception as e:
        log_event('CORRELATION_ERROR', f'Error closing position: {str(e)}')


def get_correlation_stats() -> Dict:
    """Get correlation statistics"""
    try:
        return correlation_analyzer.get_correlation_statistics()
    except Exception as e:
        log_event('CORRELATION_ERROR', f'Error getting stats: {str(e)}')
        return {}


def get_sector_exposure() -> Dict[str, List[str]]:
    """Get sector exposure"""
    try:
        return correlation_analyzer.get_sector_exposure()
    except Exception as e:
        log_event('CORRELATION_ERROR', f'Error getting exposure: {str(e)}')
        return {}
