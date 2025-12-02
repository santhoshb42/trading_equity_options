# Analytics Package

"""
This package contains the analytics framework for achieving 5% daily growth target using margin leverage strategy.

Target Strategy:
- Total Capital: ₹20,000
- Margin per trade: 20% (₹4,000 per position)
- Maximum positions: 5 simultaneous trades
- Target per trade: 1% minimum profit
- Daily goal: 5% total capital growth (₹1,000 profit)

Components:
- target_analytics.py: Core analytics engine
- api_endpoints.py: FastAPI endpoints for monitoring
- Integration with main trading system

Usage:
    from eqcode.analytics import TargetAnalytics
    analytics = TargetAnalytics()
"""

from .target_analytics import TargetAnalytics

__all__ = ['TargetAnalytics']