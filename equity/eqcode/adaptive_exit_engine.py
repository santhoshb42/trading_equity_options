"""
Adaptive Exit Engine - Smart Multi-Stage Position Management

Combines Phase 3 (Multi-Stage Exit) and Phase 4 (Adaptive SL Buffer)
into a unified intelligent exit system.

Key Features:
1. Time-based stages (Initial, Momentum, Profit, Extended)
2. Profit-based adaptive trailing buffer
3. Early exit on momentum loss
4. ML feedback on every exit
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from collections import deque
import json


def log_event(*args, **kwargs):
    """Stub for logging integration"""
    pass


class ExitReason:
    """Exit reason constants with ML training categories"""
    
    # Standard exits
    SL_HIT = "SL_HIT"                           # ML: Loss
    TARGET_HIT = "TARGET_HIT"                   # ML: Win
    TRAILING_SL = "TRAILING_SL"                 # ML: Win (protected profit)
    
    # Early exits (new)
    MOMENTUM_LOSS = "MOMENTUM_LOSS"             # ML: Loss (early detection)
    TIME_STAGNANT = "TIME_STAGNANT"             # ML: Neutral (opportunity cost)
    VOLUME_DRIED = "VOLUME_DRIED"               # ML: Loss (weak signal)
    DECLINING_PATTERN = "DECLINING_PATTERN"     # ML: Loss (price weakness)
    
    # Stage-based exits (new)
    STAGE1_TIMEOUT = "STAGE1_TIMEOUT"           # ML: Loss (failed quick move)
    STAGE2_BREAKEVEN = "STAGE2_BREAKEVEN"       # ML: Win (protected capital)
    STAGE3_PROFIT_LOCK = "STAGE3_PROFIT_LOCK"   # ML: Win (locked profit)
    STAGE4_TIME_DECAY = "STAGE4_TIME_DECAY"     # ML: Win (time-based exit)
    
    # Adaptive trailing exits (new)
    ADAPTIVE_TIGHT_TRAIL = "ADAPTIVE_TIGHT_TRAIL"     # Small winner, tight trail
    ADAPTIVE_LOOSE_TRAIL = "ADAPTIVE_LOOSE_TRAIL"     # Big winner, loose trail
    ADAPTIVE_MAX_TRAIL = "ADAPTIVE_MAX_TRAIL"         # Huge winner, max buffer


class PositionStage:
    """Position lifecycle stages"""
    INITIAL = "INITIAL"           # 0-5 min: Tight protection
    MOMENTUM = "MOMENTUM"         # 5-15 min: Confirming direction
    PROFIT = "PROFIT"             # 15-30 min: In profit, protect gains
    EXTENDED = "EXTENDED"         # 30+ min: Time decay, lock profits


class AdaptiveExitEngine:
    """
    Manages position exits with adaptive logic based on:
    - Time since entry
    - Profit/loss level
    - Price momentum
    - Market conditions
    """
    
    def __init__(self):
        self.position_tracking = {}  # symbol -> tracking data
        
        # Configuration
        self.config = {
            # Stage timing (minutes)
            'stage1_duration': 5,
            'stage2_duration': 15,
            'stage3_duration': 30,
            
            # Initial stage (0-5 min)
            'stage1_timeout_loss_threshold': -0.2,  # Exit if worse than -0.2%
            'stage1_no_profit_timeout': 180,        # 3 minutes
            
            # Momentum confirmation (5-15 min)
            'stage2_breakeven_trigger': 0.3,        # Move to BE at +0.3%
            'stage2_stagnation_timeout': 600,       # 10 min flat = exit (increased from 5 min to allow consolidations)
            
            # 🆕 SMART PROFIT PROTECTION (immediate locking)
            'quick_profit_lock_trigger': 0.4,       # At +0.4%, lock at +0.2%
            'medium_profit_lock_trigger': 0.7,      # At +0.7%, lock at +0.4%
            'large_profit_lock_trigger': 1.2,       # At +1.2%, lock at +0.8%
            'huge_profit_lock_trigger': 2.0,        # At +2.0%, lock at +1.5%
            
            'quick_profit_lock_level': 0.2,         # Lock 50% of profit
            'medium_profit_lock_level': 0.4,        # Lock 57% of profit
            'large_profit_lock_level': 0.8,         # Lock 67% of profit
            'huge_profit_lock_level': 1.5,          # Lock 75% of profit
            
            # Sudden dip protection
            'sudden_dip_threshold': -0.3,           # -0.3% in 1 check (20s)
            'profit_erosion_threshold': 0.5,        # If gave back >50% of max profit
            
            # 🆕 DUAL-MODE ADAPTIVE TRAILING (Time-based scalp vs runner)
            # ====================================================================
            # EQUITY PROFIT RANGE: 0-4% max (NSE equity constraints)
            # Each mode defines milestones from 0.5% profit to 4%+ profit
            
            # SCALP MODE (9:30-9:45 AM): Aggressive trailing for quick peaks
            # - Tight buffers to capture momentum at peaks
            # - Quick exits on dips
            # - Lock profits immediately
            'scalp_buffer_tiny': 0.15,      # 0.0-0.5% profit (scalp mode)
            'scalp_buffer_small': 0.20,     # 0.5-1.0% profit (scalp mode)
            'scalp_buffer_medium': 0.30,    # 1.0-1.5% profit (scalp mode)
            'scalp_buffer_large': 0.40,     # 1.5-2.5% profit (scalp mode)
            'scalp_buffer_xlarge': 0.45,    # 2.5-3.0% profit (scalp mode) - NEW
            'scalp_buffer_xxlarge': 0.48,   # 3.0-3.5% profit (scalp mode) - NEW
            'scalp_buffer_huge': 0.50,      # 3.5-4.0% profit (scalp mode) - TIGHTENED
            'scalp_buffer_max': 0.50,       # 4.0%+ profit (scalp mode) - LOCKED
            
            # RUNNER MODE (9:45+ AM): Loose trailing for multi-minute runners
            # - Wider buffers to let profits run
            # - Slower SL updates
            # - Capture extended moves
            'runner_buffer_tiny': 0.35,     # 0.0-0.5% profit (runner mode)
            'runner_buffer_small': 0.50,    # 0.5-1.0% profit (runner mode)
            'runner_buffer_medium': 0.70,   # 1.0-1.5% profit (runner mode)
            'runner_buffer_large': 1.0,     # 1.5-2.5% profit (runner mode)
            'runner_buffer_xlarge': 1.10,   # 2.5-3.0% profit (runner mode) - NEW
            'runner_buffer_xxlarge': 1.15,  # 3.0-3.5% profit (runner mode) - NEW
            'runner_buffer_huge': 1.18,     # 3.5-4.0% profit (runner mode) - TIGHTENED
            'runner_buffer_max': 1.20,      # 4.0%+ profit (runner mode) - LOCKED
            
            # Legacy mode (fallback): Use standard buffers
            'buffer_tiny': 0.2,      # 0.0-0.5% profit
            'buffer_small': 0.3,     # 0.5-1.0% profit
            'buffer_medium': 0.5,    # 1.0-1.5% profit
            'buffer_large': 0.8,     # 1.5-2.5% profit
            'buffer_huge': 1.0,      # 2.5%+ profit
            
            # Extended stage (30+ min) - time decay
            'stage4_buffer_decay': True,
            'stage4_30min_buffer': 0.6,
            'stage4_45min_buffer': 0.4,
            'stage4_60min_buffer': 0.3,
            
            # Momentum detection
            'momentum_checks': 3,                   # Need 3 consecutive drops
            'momentum_decline_threshold': -0.1,     # Each drop > 0.1%
        }
    
    def start_tracking(self, symbol: str, entry_price: float, entry_time: datetime):
        """Start tracking a position for adaptive exit"""
        self.position_tracking[symbol] = {
            'entry_price': entry_price,
            'entry_time': entry_time,
            'stage': PositionStage.INITIAL,
            'highest_price': entry_price,
            'highest_profit_pct': 0.0,
            'last_price': entry_price,
            'last_profit_check': entry_time,
            'price_history': deque(maxlen=5),  # Last 5 LTP checks
            'stagnation_start': None,
            'breakeven_moved': False,
            'profit_locked': False,
            'locked_profit_level': 0.0,
            'stage_transitions': [],
        }
        
        log_event("ADAPTIVE_EXIT_START", f"Started adaptive exit tracking for {symbol}",
                 symbol=symbol, entry_price=entry_price, stage=PositionStage.INITIAL)
    
    def stop_tracking(self, symbol: str):
        """Stop tracking a position"""
        if symbol in self.position_tracking:
            del self.position_tracking[symbol]
    
    def update_price(self, symbol: str, current_ltp: float, current_time: datetime):
        """Update price and track momentum"""
        if symbol not in self.position_tracking:
            return
        
        tracking = self.position_tracking[symbol]
        entry_price = tracking['entry_price']
        
        # Calculate current profit
        current_profit_pct = ((current_ltp - entry_price) / entry_price) * 100
        
        # Track price history
        tracking['price_history'].append({
            'price': current_ltp,
            'time': current_time,
            'profit_pct': current_profit_pct
        })
        
        # Update highest price and profit
        if current_ltp > tracking['highest_price']:
            tracking['highest_price'] = current_ltp
            tracking['stagnation_start'] = None  # Reset stagnation
        
        if current_profit_pct > tracking['highest_profit_pct']:
            tracking['highest_profit_pct'] = current_profit_pct
        
        # Store last price for sudden dip detection
        tracking['last_price'] = current_ltp
    
    def get_current_stage(self, symbol: str) -> str:
        """Determine current stage based on time and profit"""
        if symbol not in self.position_tracking:
            return PositionStage.INITIAL
        
        tracking = self.position_tracking[symbol]
        elapsed_minutes = (datetime.now() - tracking['entry_time']).total_seconds() / 60
        
        if elapsed_minutes < self.config['stage1_duration']:
            return PositionStage.INITIAL
        elif elapsed_minutes < self.config['stage2_duration']:
            return PositionStage.MOMENTUM
        elif elapsed_minutes < self.config['stage3_duration']:
            return PositionStage.PROFIT
        else:
            return PositionStage.EXTENDED
    
    def get_adaptive_buffer(self, symbol: str, current_ltp: float, elapsed_minutes: float) -> float:
        """
        Calculate adaptive trailing buffer based on:
        1. Entry time (scalp vs runner mode)
        2. Profit level (0.5% to 4%+ in equity)
        3. Time elapsed (decay after 30 min)
        
        EQUITY CONSTRAINT: Max 4% profit (NSE equity limits)
        
        Returns buffer percentage (e.g., 0.8 for 0.8%)
        """
        if symbol not in self.position_tracking:
            return 0.3  # Default
        
        tracking = self.position_tracking[symbol]
        entry_price = tracking['entry_price']
        entry_time = tracking['entry_time']
        profit_pct = ((current_ltp - entry_price) / entry_price) * 100
        
        # Determine if in SCALP MODE (9:30-9:45 AM) or RUNNER MODE (9:45+ AM)
        hour = entry_time.hour
        minute = entry_time.minute
        is_scalp_mode = (hour == 9 and minute >= 30 and minute <= 45) or \
                        (hour == 10 and minute <= 45)  # Allow 10:00-10:45 if alert was late
        
        # Select buffer config based on mode with proper 0-4% EQUITY RANGE
        if is_scalp_mode:
            # SCALP MODE: Use aggressive tight trailing buffers
            # Profit milestones: 0.5%, 1.0%, 1.5%, 2.5%, 3.0%, 3.5%, 4.0%+
            if profit_pct < 0.5:
                base_buffer = self.config['scalp_buffer_tiny']           # 0.15%
            elif profit_pct < 1.0:
                base_buffer = self.config['scalp_buffer_small']          # 0.20%
            elif profit_pct < 1.5:
                base_buffer = self.config['scalp_buffer_medium']         # 0.30%
            elif profit_pct < 2.5:
                base_buffer = self.config['scalp_buffer_large']          # 0.40%
            elif profit_pct < 3.0:
                base_buffer = self.config['scalp_buffer_xlarge']         # 0.45%
            elif profit_pct < 3.5:
                base_buffer = self.config['scalp_buffer_xxlarge']        # 0.48%
            elif profit_pct < 4.0:
                base_buffer = self.config['scalp_buffer_huge']           # 0.50%
            else:
                base_buffer = self.config['scalp_buffer_max']            # 0.50% (LOCKED at 4%+)
        else:
            # RUNNER MODE: Use loose trailing buffers for extended moves
            # Profit milestones: 0.5%, 1.0%, 1.5%, 2.5%, 3.0%, 3.5%, 4.0%+
            if profit_pct < 0.5:
                base_buffer = self.config['runner_buffer_tiny']          # 0.35%
            elif profit_pct < 1.0:
                base_buffer = self.config['runner_buffer_small']         # 0.50%
            elif profit_pct < 1.5:
                base_buffer = self.config['runner_buffer_medium']        # 0.70%
            elif profit_pct < 2.5:
                base_buffer = self.config['runner_buffer_large']         # 1.00%
            elif profit_pct < 3.0:
                base_buffer = self.config['runner_buffer_xlarge']        # 1.10%
            elif profit_pct < 3.5:
                base_buffer = self.config['runner_buffer_xxlarge']       # 1.15%
            elif profit_pct < 4.0:
                base_buffer = self.config['runner_buffer_huge']          # 1.18%
            else:
                base_buffer = self.config['runner_buffer_max']           # 1.20% (LOCKED at 4%+)

        # Apply time decay in extended stage (30+ min) - overrides mode
        if elapsed_minutes >= 30 and self.config['stage4_buffer_decay']:
            if elapsed_minutes >= 60:
                base_buffer = min(base_buffer, self.config['stage4_60min_buffer'])
            elif elapsed_minutes >= 45:
                base_buffer = min(base_buffer, self.config['stage4_45min_buffer'])
            elif elapsed_minutes >= 30:
                base_buffer = min(base_buffer, self.config['stage4_30min_buffer'])
        
        # Log the mode detection for debugging
        mode_label = "SCALP" if is_scalp_mode else "RUNNER"
        log_event("BUFFER_MODE_DETECTED", f"Using {mode_label} mode for {symbol}",
                 symbol=symbol,
                 mode=mode_label,
                 entry_hour=hour,
                 entry_minute=minute,
                 profit_pct=round(profit_pct, 2),
                 base_buffer=base_buffer,
                 elapsed_minutes=round(elapsed_minutes, 1),
                 profit_range="0-4% EQUITY CONSTRAINT")
        
        return base_buffer
    
    def calculate_adaptive_sl(self, symbol: str, current_ltp: float, base_sl: float) -> Tuple[float, str]:
        """
        Calculate adaptive stop loss with buffer AND smart profit locking
        
        Returns:
            (adjusted_sl, exit_reason_type)
        """
        if symbol not in self.position_tracking:
            return base_sl, ExitReason.TRAILING_SL
        
        tracking = self.position_tracking[symbol]
        entry_price = tracking['entry_price']
        elapsed_minutes = (datetime.now() - tracking['entry_time']).total_seconds() / 60
        
        profit_pct = ((current_ltp - entry_price) / entry_price) * 100
        
        # 🆕 SMART PROFIT LOCKING: Immediately lock profits at key levels
        locked_sl = self._calculate_profit_lock_sl(symbol, current_ltp, profit_pct, entry_price)
        
        if locked_sl > base_sl:
            # Profit lock SL is better than trailing SL - use it!
            log_event("PROFIT_LOCK_APPLIED", f"💰 Using profit-locked SL for {symbol}",
                     symbol=symbol,
                     profit_pct=round(profit_pct, 2),
                     base_sl=round(base_sl, 2),
                     locked_sl=round(locked_sl, 2),
                     improvement=round(((locked_sl - base_sl) / base_sl) * 100, 2),
                     locked_level_pct=tracking.get('locked_profit_level', 0),
                     locked_profit_pct=round(((locked_sl - entry_price) / entry_price) * 100, 2),
                     current_ltp=current_ltp,
                     action="Returning locked SL instead of base trailing SL")
            return locked_sl, ExitReason.STAGE3_PROFIT_LOCK
        
        # Get adaptive buffer for trailing SL
        buffer_pct = self.get_adaptive_buffer(symbol, current_ltp, elapsed_minutes)
        
        # Calculate adjusted SL with buffer
        adjusted_sl = base_sl - (entry_price * buffer_pct / 100)
        
        # Determine exit reason type based on buffer level
        if buffer_pct >= 1.0:
            exit_type = ExitReason.ADAPTIVE_MAX_TRAIL
        elif buffer_pct >= 0.7:
            exit_type = ExitReason.ADAPTIVE_LOOSE_TRAIL
        else:
            exit_type = ExitReason.ADAPTIVE_TIGHT_TRAIL
        
        log_event("ADAPTIVE_BUFFER_APPLIED", f"Adaptive buffer calculated for {symbol}",
                 symbol=symbol,
                 profit_pct=round(profit_pct, 2),
                 buffer_pct=buffer_pct,
                 base_sl=base_sl,
                 adjusted_sl=adjusted_sl,
                 buffer_amount=round(base_sl - adjusted_sl, 2),
                 exit_type=exit_type,
                 reason=f"Buffer {buffer_pct}% applied")
        
        log_event("ADAPTIVE_SL_CALC", f"Adaptive SL for {symbol}",
                 symbol=symbol,
                 profit_pct=round(profit_pct, 2),
                 base_sl=round(base_sl, 2),
                 buffer_pct=buffer_pct,
                 adjusted_sl=round(adjusted_sl, 2),
                 elapsed_min=round(elapsed_minutes, 1),
                 exit_type=exit_type)
        
        return adjusted_sl, exit_type
    
    def _calculate_profit_lock_sl(self, symbol: str, current_ltp: float, profit_pct: float, entry_price: float) -> float:
        """
        Calculate smart profit locking SL
        
        Strategy: When profit reaches certain milestones, immediately lock a portion
        - At +0.4% profit → Lock at +0.2% (lock 50%)
        - At +0.7% profit → Lock at +0.4% (lock 57%)
        - At +1.2% profit → Lock at +0.8% (lock 67%)
        - At +2.0% profit → Lock at +1.5% (lock 75%)
        
        This ensures we don't lose profits to sudden dips!
        """
        tracking = self.position_tracking[symbol]
        
        # Check if we've already locked profit at a higher level
        current_locked = tracking.get('locked_profit_level', 0.0)
        
        # Determine appropriate lock level based on current profit
        if profit_pct >= self.config['huge_profit_lock_trigger'] and current_locked < self.config['huge_profit_lock_level']:
            # Lock 75% of 2% profit = 1.5%
            tracking['locked_profit_level'] = self.config['huge_profit_lock_level']
            tracking['profit_locked'] = True
            locked_sl = entry_price * (1 + self.config['huge_profit_lock_level'] / 100)
            log_event("PROFIT_LOCK_TRIGGERED", f"🔒 Profit lock activated for {symbol} at +2.0% milestone",
                     symbol=symbol,
                     profit_pct=round(profit_pct, 2),
                     locked_level_pct=self.config['huge_profit_lock_level'],
                     saved_profit_pct=75,
                     entry_price=entry_price,
                     current_ltp=current_ltp,
                     locked_sl=locked_sl,
                     reason="Hit +2.0% profit milestone")
            return locked_sl
        
        elif profit_pct >= self.config['large_profit_lock_trigger'] and current_locked < self.config['large_profit_lock_level']:
            # Lock 67% of 1.2% profit = 0.8%
            tracking['locked_profit_level'] = self.config['large_profit_lock_level']
            tracking['profit_locked'] = True
            locked_sl = entry_price * (1 + self.config['large_profit_lock_level'] / 100)
            log_event("PROFIT_LOCK_TRIGGERED", f"🔒 Profit lock activated for {symbol} at +1.2% milestone",
                     symbol=symbol,
                     profit_pct=round(profit_pct, 2),
                     locked_level_pct=self.config['large_profit_lock_level'],
                     saved_profit_pct=67,
                     entry_price=entry_price,
                     current_ltp=current_ltp,
                     locked_sl=locked_sl,
                     reason="Hit +1.2% profit milestone")
            return locked_sl
        
        elif profit_pct >= self.config['medium_profit_lock_trigger'] and current_locked < self.config['medium_profit_lock_level']:
            # Lock 57% of 0.7% profit = 0.4%
            tracking['locked_profit_level'] = self.config['medium_profit_lock_level']
            tracking['profit_locked'] = True
            locked_sl = entry_price * (1 + self.config['medium_profit_lock_level'] / 100)
            log_event("PROFIT_LOCK_TRIGGERED", f"🔒 Profit lock activated for {symbol} at +0.7% milestone",
                     symbol=symbol,
                     profit_pct=round(profit_pct, 2),
                     locked_level_pct=self.config['medium_profit_lock_level'],
                     saved_profit_pct=57,
                     entry_price=entry_price,
                     current_ltp=current_ltp,
                     locked_sl=locked_sl,
                     reason="Hit +0.7% profit milestone")
            return locked_sl
        
        elif profit_pct >= self.config['quick_profit_lock_trigger'] and current_locked < self.config['quick_profit_lock_level']:
            # Lock 50% of 0.4% profit = 0.2%
            tracking['locked_profit_level'] = self.config['quick_profit_lock_level']
            tracking['profit_locked'] = True
            locked_sl = entry_price * (1 + self.config['quick_profit_lock_level'] / 100)
            log_event("PROFIT_LOCK_TRIGGERED", f"🔒 Profit lock activated for {symbol} at +0.4% milestone",
                     symbol=symbol,
                     profit_pct=round(profit_pct, 2),
                     locked_level_pct=self.config['quick_profit_lock_level'],
                     saved_profit_pct=50,
                     entry_price=entry_price,
                     current_ltp=current_ltp,
                     locked_sl=locked_sl,
                     reason="Hit +0.4% profit milestone")
            return locked_sl
        
        # No profit lock triggered, return 0 (will use normal trailing)
        if profit_pct > 0.1:  # Only log if we have some profit
            log_event("PROFIT_LOCK_CHECK", f"Profit lock checked for {symbol}",
                     symbol=symbol,
                     profit_pct=round(profit_pct, 2),
                     current_locked_pct=current_locked,
                     next_milestone=0.4 if profit_pct < 0.4 else (0.7 if profit_pct < 0.7 else (1.2 if profit_pct < 1.2 else 2.0)),
                     status="No lock triggered yet")
        return 0.0
    
    def check_early_exit(self, symbol: str, current_ltp: float) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Check if position should exit early (before SL hit)
        
        Returns:
            (exit_reason, details) if should exit, else None
        """
        if symbol not in self.position_tracking:
            return None
        
        tracking = self.position_tracking[symbol]
        entry_price = tracking['entry_price']
        entry_time = tracking['entry_time']
        current_time = datetime.now()
        
        elapsed_seconds = (current_time - entry_time).total_seconds()
        elapsed_minutes = elapsed_seconds / 60
        profit_pct = ((current_ltp - entry_price) / entry_price) * 100
        
        # 🆕 CHECK 1: Sudden Dip Detection (works in ALL stages)
        if len(tracking['price_history']) >= 2:
            last_price_data = tracking['price_history'][-2]
            last_price = last_price_data['price']
            price_change_pct = ((current_ltp - last_price) / last_price) * 100
            
            if price_change_pct <= self.config['sudden_dip_threshold']:
                # Sudden dip detected!
                log_event("EARLY_EXIT_SUDDEN_DIP", f"🚨 Sudden dip detected for {symbol}",
                         symbol=symbol,
                         price_drop_pct=round(price_change_pct, 2),
                         prev_price=last_price,
                         current_ltp=current_ltp,
                         threshold=self.config['sudden_dip_threshold'],
                         action="Triggering early exit")
                return (ExitReason.DECLINING_PATTERN, {
                    'elapsed_min': round(elapsed_minutes, 1),
                    'profit_pct': round(profit_pct, 2),
                    'dip_pct': round(price_change_pct, 2),
                    'reason': f'Sudden dip: {price_change_pct:.2f}% in 20 seconds'
                })
        
        # 🆕 CHECK 2: Profit Erosion Detection (gave back too much profit)
        if tracking['highest_profit_pct'] > 0.5:  # Only if we had decent profit
            profit_given_back = tracking['highest_profit_pct'] - profit_pct
            erosion_pct = profit_given_back / tracking['highest_profit_pct']
            
            if erosion_pct >= self.config['profit_erosion_threshold']:
                # Gave back >50% of max profit!
                log_event("EARLY_EXIT_PROFIT_EROSION", f"⚠️ Profit erosion detected for {symbol}",
                         symbol=symbol,
                         max_profit_pct=round(tracking['highest_profit_pct'], 2),
                         current_profit_pct=round(profit_pct, 2),
                         gave_back_pct=round(profit_given_back, 2),
                         erosion_pct=round(erosion_pct * 100, 1),
                         threshold=f"{self.config['profit_erosion_threshold']*100}% of max profit",
                         action="Triggering early exit to save remaining profit")
                return (ExitReason.MOMENTUM_LOSS, {
                    'elapsed_min': round(elapsed_minutes, 1),
                    'profit_pct': round(profit_pct, 2),
                    'max_profit_pct': round(tracking['highest_profit_pct'], 2),
                    'gave_back_pct': round(profit_given_back, 2),
                    'erosion_pct': round(erosion_pct * 100, 1),
                    'reason': f'Gave back {erosion_pct*100:.0f}% of max profit'
                })
        
        stage = self.get_current_stage(symbol)
        
        # STAGE 1: Initial Protection (0-5 min)
        if stage == PositionStage.INITIAL:
            # Check 1: No profit after 3 minutes
            if elapsed_seconds >= self.config['stage1_no_profit_timeout'] and profit_pct <= 0:
                return (ExitReason.STAGE1_TIMEOUT, {
                    'elapsed_min': round(elapsed_minutes, 1),
                    'profit_pct': round(profit_pct, 2),
                    'reason': 'No profit after 3 minutes'
                })
            
            # Check 2: Declining momentum (3 consecutive drops)
            if self._detect_declining_momentum(symbol):
                log_event("EARLY_EXIT_MOMENTUM_LOSS", f"📉 Declining momentum detected for {symbol}",
                         symbol=symbol,
                         consecutive_drops=3,
                         elapsed_minutes=round(elapsed_minutes, 1),
                         profit_pct=round(profit_pct, 2),
                         threshold="3 consecutive price drops",
                         action="Triggering early exit")
                return (ExitReason.DECLINING_PATTERN, {
                    'elapsed_min': round(elapsed_minutes, 1),
                    'profit_pct': round(profit_pct, 2),
                    'reason': '3 consecutive price drops detected'
                })
        
        # STAGE 2: Momentum Confirmation (5-15 min)
        elif stage == PositionStage.MOMENTUM:
            # Check 1: Stagnation (flat for 5+ minutes)
            if self._detect_stagnation(symbol, current_time):
                log_event("EARLY_EXIT_STAGNATION", f"⏱️ Time stagnation detected for {symbol}",
                         symbol=symbol,
                         stage="MOMENTUM",
                         elapsed_minutes=round(elapsed_minutes, 1),
                         profit_pct=round(profit_pct, 2),
                         threshold="Price flat for 5+ min",
                         action="Triggering early exit")
                return (ExitReason.TIME_STAGNANT, {
                    'elapsed_min': round(elapsed_minutes, 1),
                    'profit_pct': round(profit_pct, 2),
                    'reason': 'Price stagnant for 5+ minutes'
                })
            
            # Check 2: Small profit but declining
            if 0 < profit_pct < 0.3 and self._detect_declining_momentum(symbol):
                return (ExitReason.MOMENTUM_LOSS, {
                    'elapsed_min': round(elapsed_minutes, 1),
                    'profit_pct': round(profit_pct, 2),
                    'reason': 'Small profit but losing momentum'
                })
        
        # STAGE 3 & 4: Let adaptive trailing handle exits
        # No early exits in profit protection stages
        
        return None
    
    def _detect_declining_momentum(self, symbol: str) -> bool:
        """Detect if price is showing declining momentum (3 consecutive drops)"""
        if symbol not in self.position_tracking:
            return False
        
        tracking = self.position_tracking[symbol]
        price_history = tracking['price_history']
        
        if len(price_history) < self.config['momentum_checks']:
            return False
        
        # Check last 3 prices
        prices = [p['price'] for p in price_history]
        last_3 = prices[-3:]
        
        # Each price should be lower than previous
        declining_count = 0
        for i in range(1, len(last_3)):
            if last_3[i] < last_3[i-1]:
                pct_drop = ((last_3[i] - last_3[i-1]) / last_3[i-1]) * 100
                if pct_drop <= self.config['momentum_decline_threshold']:
                    declining_count += 1
        
        return declining_count >= 2  # At least 2 drops
    
    def _detect_stagnation(self, symbol: str, current_time: datetime) -> bool:
        """Detect if price has been stagnant (±0.1%) for too long"""
        if symbol not in self.position_tracking:
            return False
        
        tracking = self.position_tracking[symbol]
        price_history = tracking['price_history']
        
        if len(price_history) < 3:
            return False
        
        # Only check recent prices (last 10 minutes worth) for stagnation, not entire history
        # This prevents the entire entry-to-now range from blocking stagnation detection
        stagnation_window_seconds = 600  # 10 minutes - check only last 10 min of prices
        cutoff_time = current_time - timedelta(seconds=stagnation_window_seconds)
        recent_prices = [p for p in price_history if p['time'] >= cutoff_time]
        
        # If we don't have recent data, use all available
        if not recent_prices:
            recent_prices = price_history[-10:] if len(price_history) >= 10 else price_history
        
        # Check if all recent prices are within ±0.1% of each other
        prices = [p['price'] for p in recent_prices]
        max_price = max(prices)
        min_price = min(prices)
        range_pct = ((max_price - min_price) / min_price) * 100
        
        if range_pct <= 0.2:  # Very narrow range (±0.1%)
            if tracking['stagnation_start'] is None:
                tracking['stagnation_start'] = current_time
            else:
                stagnant_duration = (current_time - tracking['stagnation_start']).total_seconds()
                if stagnant_duration >= self.config['stage2_stagnation_timeout']:
                    return True
        else:
            tracking['stagnation_start'] = None
        
        return False
    
    def should_move_to_breakeven(self, symbol: str, current_ltp: float) -> bool:
        """Check if SL should be moved to breakeven"""
        if symbol not in self.position_tracking:
            return False
        
        tracking = self.position_tracking[symbol]
        
        if tracking['breakeven_moved']:
            return False
        
        entry_price = tracking['entry_price']
        profit_pct = ((current_ltp - entry_price) / entry_price) * 100
        
        if profit_pct >= self.config['stage2_breakeven_trigger']:
            tracking['breakeven_moved'] = True
            return True
        
        return False
    
    def get_exit_summary(self, symbol: str, exit_reason: str, exit_price: float) -> Dict[str, Any]:
        """Generate comprehensive exit summary with ML feedback data"""
        if symbol not in self.position_tracking:
            return {'exit_reason': exit_reason}
        
        tracking = self.position_tracking[symbol]
        entry_price = tracking['entry_price']
        entry_time = tracking['entry_time']
        exit_time = datetime.now()
        
        elapsed_seconds = (exit_time - entry_time).total_seconds()
        profit_pct = ((exit_price - entry_price) / entry_price) * 100
        profit_amount = (exit_price - entry_price)
        
        # Determine if this was a win/loss for ML
        ml_outcome = 'WIN' if profit_pct > 0 else 'LOSS'
        
        log_event("EXIT_SUMMARY_GENERATED", f"📊 Generating exit summary for {symbol}",
                 symbol=symbol,
                 entry_price=entry_price,
                 exit_price=exit_price,
                 profit_pct=round(profit_pct, 2),
                 max_profit_pct=round(((tracking['highest_price'] - entry_price) / entry_price) * 100, 2),
                 gave_back_pct=round(((tracking['highest_price'] - exit_price) / entry_price) * 100, 2),
                 hold_minutes=round(elapsed_seconds / 60, 1),
                 exit_reason=exit_reason,
                 ml_outcome=ml_outcome)
        
        summary = {
            'symbol': symbol,
            'exit_reason': exit_reason,
            'exit_reason_category': self._categorize_exit_reason(exit_reason),
            'entry_price': entry_price,
            'exit_price': exit_price,
            'profit_pct': round(profit_pct, 2),
            'profit_amount': round(profit_amount, 2),
            'hold_duration_seconds': int(elapsed_seconds),
            'hold_duration_minutes': round(elapsed_seconds / 60, 1),
            'highest_price': tracking['highest_price'],
            'max_profit_pct': round(((tracking['highest_price'] - entry_price) / entry_price) * 100, 2),
            'gave_back_pct': round(((tracking['highest_price'] - exit_price) / entry_price) * 100, 2),
            'ml_outcome': ml_outcome,
            'stage_at_exit': self.get_current_stage(symbol),
            'stage_transitions': tracking['stage_transitions'],
        }
        
        return summary
    
    def _categorize_exit_reason(self, exit_reason: str) -> str:
        """Categorize exit reason for ML training"""
        early_exits = [
            ExitReason.MOMENTUM_LOSS,
            ExitReason.TIME_STAGNANT,
            ExitReason.VOLUME_DRIED,
            ExitReason.DECLINING_PATTERN,
            ExitReason.STAGE1_TIMEOUT
        ]
        
        if exit_reason in early_exits:
            return 'EARLY_EXIT'
        elif 'TRAIL' in exit_reason:
            return 'TRAILING_EXIT'
        elif exit_reason == ExitReason.SL_HIT:
            return 'SL_HIT'
        elif exit_reason == ExitReason.TARGET_HIT:
            return 'TARGET_HIT'
        else:
            return 'STANDARD_EXIT'


# Global instance
_adaptive_exit_engine = None


def get_adaptive_exit_engine() -> AdaptiveExitEngine:
    """Get or create adaptive exit engine instance"""
    global _adaptive_exit_engine
    if _adaptive_exit_engine is None:
        _adaptive_exit_engine = AdaptiveExitEngine()
    return _adaptive_exit_engine


def record_exit_for_ml(exit_summary: Dict[str, Any]):
    """Record exit details for ML training"""
    try:
        from .ml_signal_filter import record_ml_trade_outcome
        
        symbol = exit_summary['symbol']
        ml_outcome = exit_summary['ml_outcome']
        won = (ml_outcome == 'WIN')
        
        # Record outcome
        record_ml_trade_outcome(symbol, won)
        
        log_event("ML_EXIT_RECORDED", f"Recorded exit for ML training: {symbol}",
                 symbol=symbol,
                 won=won,
                 profit_pct=exit_summary['profit_pct'],
                 exit_reason=exit_summary['exit_reason'],
                 hold_duration=exit_summary['hold_duration_minutes'])
        
    except Exception as e:
        log_event("ML_EXIT_ERROR", f"Failed to record exit for ML: {e}")
