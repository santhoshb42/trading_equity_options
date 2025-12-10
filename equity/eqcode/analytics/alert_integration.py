"""
Alert Tracking Integration

Simple, non-intrusive integration for tracking missed alerts.
Add this to existing webhook handler for comprehensive analytics.

DESIGN PRINCIPLES:
1. Fail-safe: Never break trading if analytics fails
2. Non-blocking: All operations happen in background
3. Progressive: Can be added/removed without system changes
"""

from typing import Dict, Any
from .alert_tracker import track_alert_received, track_alert_executed, track_alert_rejected


def integrate_alert_tracking():
    """
    Integration points for existing API endpoints
    
    Usage:
    1. Call track_webhook_alert() when webhook received
    2. Call track_execution_success() when trade executed  
    3. Call track_execution_failure() when trade rejected
    """
    pass


def track_webhook_alert(alert_data: Dict[str, Any]) -> None:
    """
    Track incoming webhook alert (Phase 1: Basic logging)
    
    Args:
        alert_data: Processed alert data from webhook
        
    Integration point: Add to webhook handler after alert validation
    """
    try:
        track_alert_received(alert_data)
    except Exception:
        # Silently fail - never break trading
        pass


def track_execution_success(alert_data: Dict[str, Any], trade_result: Dict[str, Any]) -> None:
    """
    Track successful trade execution
    
    Args:
        alert_data: Original alert data
        trade_result: Trade execution results
        
    Integration point: Add after successful order placement
    """
    try:
        # 🔧 DEFENSIVE: Ensure we have valid data before passing to tracker
        if not alert_data or not isinstance(alert_data, dict):
            return
        if not trade_result or not isinstance(trade_result, dict):
            return
            
        execution_data = {
            'capital_used': trade_result.get('capital_used', 0),
            'quantity': trade_result.get('quantity', 0),
            'order_id': trade_result.get('order_id', ''),
            'execution_price': trade_result.get('execution_price', 0)
        }
        track_alert_executed(alert_data, execution_data)
    except Exception as e:
        # Silently fail - never break trading
        # Log to help debug but don't raise
        from ..bot_logging import log_event
        log_event("ANALYTICS_SILENT_FAIL", f"Analytics tracking silently failed: {str(e)}",
                 error_type=type(e).__name__)


def track_execution_failure(alert_data: Dict[str, Any], rejection_reason: str) -> None:
    """
    Track failed/rejected trade execution (MISSED OPPORTUNITY)
    
    Args:
        alert_data: Original alert data
        rejection_reason: Why the trade was rejected
        
    Integration point: Add when trade is rejected (capital/slots/validation)
    """
    try:
        track_alert_rejected(alert_data, rejection_reason)
    except Exception:
        # Silently fail - never break trading
        pass


# =============================================================================
# Example Integration Points
# =============================================================================

"""
EXAMPLE 1: Webhook Handler Integration
=====================================

# In webhook handler (after alert validation):
@app.route("/webhook", methods=["POST"])
def webhook():
    # ... existing validation code ...
    
    # 🎯 ADD THIS LINE (Phase 1 enhancement)
    track_webhook_alert(processed_alert)
    
    # ... rest of existing code ...


EXAMPLE 2: BUY Alert Handler Integration  
========================================

def handle_buy_alert(alert):
    # ... existing code ...
    
    # Check capital and slots availability
    can_trade, reason = trading_state.can_take_position(required_capital)
    if not can_trade:
        # 🎯 ADD THIS LINE (Track missed opportunity)
        track_execution_failure(alert, reason)
        
        return {"status": "rejected", "reason": reason}
    
    # ... order placement code ...
    
    if order_successful:
        # 🎯 ADD THIS LINE (Track successful execution)
        track_execution_success(alert, trade_results)
    else:
        # 🎯 ADD THIS LINE (Track order failure)
        track_execution_failure(alert, "Order placement failed")


EXAMPLE 3: Integration Check
===========================

# Test if integration is working:
from eqcode.analytics.alert_integration import get_alert_summary

summary = get_alert_summary()
print(f"Today: {summary['executed_alerts']} executed, {summary['missed_alerts']} missed")
"""