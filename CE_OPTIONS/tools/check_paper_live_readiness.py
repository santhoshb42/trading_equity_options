#!/usr/bin/env python3
"""
Options Bot Trading Mode Transition Validator

Analyzes paper trading performance and assesses readiness for live trading.

Usage:
    python3 check_paper_live_readiness.py
    
    Shows:
    - Paper trading statistics
    - Win rate, profit metrics, loss streaks
    - Per-symbol performance
    - Readiness score (0-100)
    - Live vs Paper comparison (if live data exists)
    - Recommendations for trading mode transition
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'optcode'))

from mode_transition_validator import get_mode_transition_validator


def main():
    print("\n" + "=" * 80)
    print("OPTIONS BOT - TRADING MODE TRANSITION VALIDATOR")
    print("=" * 80 + "\n")
    
    try:
        # Get validator and reload latest data
        validator = get_mode_transition_validator()
        validator.reload()
        
        # Generate comprehensive report
        report = validator.generate_transition_report()
        print(report)
        
        # Also provide JSON summary
        readiness = validator.get_paper_trading_readiness()
        
        print("\nJSON SUMMARY (for programmatic use):")
        print("-" * 80)
        import json
        print(json.dumps({
            'status': readiness['status'],
            'readiness_score': readiness['readiness_score'],
            'can_trade_live': readiness['status'] == 'READY',
            'paper_stats': readiness.get('paper_stats', {}),
            'issues': readiness.get('issues', []),
            'recommendations': readiness.get('recommendations', [])
        }, indent=2))
        
        # Return exit code based on readiness
        if readiness['status'] == 'READY':
            print("\n✅ System is READY for live trading!")
            return 0
        elif readiness['status'] == 'CAUTION':
            print("\n⚠️  System shows CAUTION - review recommendations before switching")
            return 1
        else:
            print("\n❌ System is NOT READY for live trading - more paper trading needed")
            return 2
            
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 3


if __name__ == '__main__':
    sys.exit(main())
