"""
Hybrid Learning Integration with API Webhook

Integrates hybrid learning engine with existing webhook API
- Receives alerts from Pine Script
- Ranks them using hybrid learner
- Selects top 10 for real capital, rest for paper trading
- Records outcomes for continuous learning
"""

from typing import Dict, List, Any, Tuple
from datetime import datetime
import json


class HybridLearningIntegration:
    """
    Integration layer between API webhook and hybrid learning engine
    """
    
    def __init__(self):
        from .hybrid_learning_engine import get_hybrid_engine
        self.engine = get_hybrid_engine()
        
        self.today_alerts = []  # Track all alerts received today
        self.today_real_trades = {}  # { alert_id: trade_details }
        self.today_paper_trades = {}  # { alert_id: trade_details }
    
    def process_incoming_alerts(self, alerts: List[Dict[str, Any]],
                               real_slots: int = 10) -> Dict[str, Any]:
        """
        Process incoming alerts from Pine Script
        
        Args:
            alerts: List of alerts like:
                [
                    {
                        'symbol': 'HDFC',
                        'action': 'BUY',
                        'entry_price': 2500,
                        'ml_score': 0.75,
                        'features': {
                            'momentum_3': 0.025,
                            'rsi_extreme': 1.0,
                            'volume_trend': 1.15,
                            'trend_consistency': 0.8,
                            'alert_confidence': 0.85,
                        },
                        'timestamp': '2025-11-16T10:30:00'
                    },
                    ...
                ]
            real_slots: How many real capital trades to select (default 10)
        
        Returns:
            {
                'status': 'success',
                'real_trades': [
                    {
                        'symbol': 'HDFC',
                        'score': 0.87,
                        'ml_confidence': 0.75,
                        'symbol_form': 'hot',
                        'feature_quality': 0.92,
                        'action': 'BUY (REAL CAPITAL)'
                    },
                    ...
                ],
                'paper_trades': [
                    {
                        'symbol': 'INFY',
                        'score': 0.62,
                        'action': 'BUY (PAPER TRADING - LEARNING)'
                    },
                    ...
                ],
                'summary': {
                    'total_alerts': 50,
                    'real_selected': 10,
                    'paper_selected': 40,
                    'learning_status': {...}
                }
            }
        """
        
        # Store all alerts
        self.today_alerts.extend(alerts)
        
        # Rank and select using hybrid engine
        selection = self.engine.rank_and_select(alerts, real_slots)
        
        # Format response
        response = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'total_alerts_received': len(alerts),
            'real_trades': [],
            'paper_trades': [],
            'summary': selection['summary'],
            'learning_status': selection['learning_status'],
        }
        
        # Format real trades
        for alert, score in selection['real_trades']:
            symbol = alert.get('symbol', 'UNKNOWN')
            
            # Store for later outcome recording
            alert_id = f"{symbol}_{datetime.now().timestamp()}"
            self.today_real_trades[alert_id] = {
                'alert': alert,
                'score': score,
                'timestamp': datetime.now(),
            }
            
            stats = self.engine.perf_tracker.get_symbol_stats(symbol)
            form_bonus = self.engine.perf_tracker.get_symbol_form_bonus(symbol)
            
            response['real_trades'].append({
                'symbol': symbol,
                'action': alert.get('action', 'BUY'),
                'entry_price': alert.get('entry_price', 0),
                'ml_score': alert.get('ml_score', 0),
                'final_score': round(score, 3),
                'ranking_breakdown': {
                    'symbol_form': 'HOT' if form_bonus > 0.1 else 'COLD' if form_bonus < -0.05 else 'NEUTRAL',
                    'form_bonus': round(form_bonus, 3),
                    'symbol_reliability': round(stats['reliability_score'], 3),
                    'recent_win_rate': round(stats['win_rate_last_10'], 2),
                },
                'execution': 'REAL CAPITAL (2000 INR)',
                'alert_id': alert_id,
            })
        
        # Format paper trades
        for alert, score in selection['paper_trades']:
            symbol = alert.get('symbol', 'UNKNOWN')
            
            # Store for later outcome recording
            alert_id = f"PAPER_{symbol}_{datetime.now().timestamp()}"
            self.today_paper_trades[alert_id] = {
                'alert': alert,
                'score': score,
                'timestamp': datetime.now(),
            }
            
            response['paper_trades'].append({
                'symbol': symbol,
                'action': alert.get('action', 'BUY'),
                'entry_price': alert.get('entry_price', 0),
                'final_score': round(score, 3),
                'execution': 'PAPER TRADING (LEARNING)',
                'alert_id': alert_id,
            })
        
        return response
    
    def record_trade_completion(self, alert_id: str, won: bool, 
                               profit: float) -> Dict[str, Any]:
        """
        Record the outcome of a trade for learning
        
        Args:
            alert_id: ID returned in alert processing
            won: Did the trade win?
            profit: P&L in rupees
        
        Returns:
            Learning update status
        """
        
        # Find the original alert
        is_paper = False
        trade_info = None
        
        if alert_id in self.today_real_trades:
            trade_info = self.today_real_trades[alert_id]
            is_paper = False
        elif alert_id in self.today_paper_trades:
            trade_info = self.today_paper_trades[alert_id]
            is_paper = True
        else:
            return {'status': 'error', 'message': f'Alert ID {alert_id} not found'}
        
        # Extract symbol and features from original alert
        symbol = trade_info['alert'].get('symbol', 'UNKNOWN')
        features = trade_info['alert'].get('features', {})
        
        # Record in engine
        if is_paper:
            self.engine.record_paper_trade_result(symbol, won, profit, features)
        else:
            self.engine.record_real_trade_result(symbol, won, profit, features)
        
        return {
            'status': 'success',
            'symbol': symbol,
            'trade_type': 'PAPER' if is_paper else 'REAL',
            'result': 'WIN' if won else 'LOSS',
            'profit': profit,
            'learning_recorded': True,
        }
    
    def eod_analysis(self) -> Dict[str, Any]:
        """
        Called at end of day (3:30 PM) to finalize learning
        
        FIXED: Now reads REAL trades from CSV logs and ingests them
        ENHANCED: Also simulates paper trading for missed alerts
        
        Returns:
            Analysis of today's trading and learning updates
        """
        
        # CRITICAL FIX: Read real closed trades from today's CSV log
        try:
            from .real_trade_parser import get_parser, get_trade_statistics
            
            parser = get_parser()
            # Get today's closed trades from CSV
            real_closed_trades = parser.get_today_trades()
            
            if real_closed_trades:
                # Ingest real trades into learning engine
                ingest_result = self.engine.ingest_real_trades(real_closed_trades)
                
                print(f"[EOD] Ingested {ingest_result['ingested']} real trades from CSV")
            else:
                ingest_result = {
                    'ingested': 0,
                    'failed': 0,
                    'total': 0
                }
        
        except Exception as e:
            print(f"[EOD] Error ingesting real trades: {e}")
            ingest_result = {
                'ingested': 0,
                'failed': 0,
                'error': str(e)
            }
        
        # ===== NEW: MISSED TRADE PAPER TRADING =====
        # Get missed alerts and simulate them with EOD LTP
        paper_trade_result = {
            'missed_alerts_found': 0,
            'paper_trades_simulated': 0,
            'paper_trades_failed': 0,
            'paper_pnl_total': 0.0
        }
        
        try:
            from .missed_trade_logger import get_missed_trade_logger, get_paper_trader
            
            logger = get_missed_trade_logger()
            missed_trades = logger.get_today_missed_trades()
            
            if missed_trades:
                paper_trade_result['missed_alerts_found'] = len(missed_trades)
                
                # Initialize paper trader with broker for LTP fetching
                trader = get_paper_trader(self.broker)
                
                # Simulate paper trades using EOD LTP
                successful_sims, failed_sims = trader.simulate_batch_paper_trades(missed_trades)
                
                paper_trade_result['paper_trades_simulated'] = len(successful_sims)
                paper_trade_result['paper_trades_failed'] = len(failed_sims)
                
                if successful_sims:
                    # Calculate total paper PnL
                    paper_trade_result['paper_pnl_total'] = sum(t['pnl'] for t in successful_sims)
                    
                    # Ingest simulated paper trades into learning (marked as paper)
                    for sim_trade in successful_sims:
                        try:
                            self.engine.record_paper_trade_result(
                                symbol=sim_trade['symbol'],
                                won=sim_trade['won'],
                                profit=sim_trade['pnl'],
                                features={
                                    'entry_price': sim_trade['entry_price'],
                                    'exit_price': sim_trade['exit_price'],
                                    'reason': sim_trade['reason'],
                                    'original_alert_time': sim_trade['original_alert_time']
                                }
                            )
                        except Exception as e:
                            print(f"[EOD] Failed to record paper trade for {sim_trade['symbol']}: {e}")
                    
                    print(f"[EOD] Paper traded {len(successful_sims)} missed opportunities, Total P&L: ₹{paper_trade_result['paper_pnl_total']:.2f}")
                
                # Clear today's missed trades log after processing
                logger.clear_today_missed()
        
        except Exception as e:
            print(f"[EOD] Error processing missed trades: {e}")
        
        # Now update learning with the real + paper data
        learning_update = self.engine.eod_learning_update()
        
        # Calculate today's stats
        real_count = len(self.today_real_trades)
        paper_count = len(self.today_paper_trades)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'daily_summary': {
                'real_trades_executed': real_count,
                'paper_trades_executed': paper_count,
                'total_trades': real_count + paper_count,
                'csv_trades_ingested': ingest_result.get('ingested', 0),
                'missed_alerts_paper_traded': paper_trade_result.get('paper_trades_simulated', 0),
                'paper_pnl_total': paper_trade_result.get('paper_pnl_total', 0.0),
            },
            'csv_ingestion': ingest_result,
            'paper_trading': paper_trade_result,
            'learning_updates': learning_update,
            'next_action': 'Model updated with REAL trade data + PAPER missed opportunities - ready for tomorrow\'s trading',
        }
    
    def get_current_status(self) -> Dict[str, Any]:
        """Get current learning status"""
        stats = self.engine.get_learning_stats()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'today_real_trades': len(self.today_real_trades),
            'today_paper_trades': len(self.today_paper_trades),
            'feature_importance': stats['feature_importance'],
            'top_performing_symbols': stats['top_symbols'],
        }


# Global integration instance
_integration = None


def get_hybrid_integration() -> HybridLearningIntegration:
    """Get or create integration instance"""
    global _integration
    if _integration is None:
        _integration = HybridLearningIntegration()
    return _integration


def process_webhook_alerts(alerts: List[Dict[str, Any]], 
                          real_slots: int = 10) -> Dict[str, Any]:
    """
    Main webhook integration function
    
    Called when alerts arrive from Pine Script
    Returns ranked selection of trades to execute
    """
    integration = get_hybrid_integration()
    return integration.process_incoming_alerts(alerts, real_slots)


def finalize_trade_learning(alert_id: str, won: bool, profit: float) -> Dict[str, Any]:
    """Record trade outcome for learning"""
    integration = get_hybrid_integration()
    return integration.record_trade_completion(alert_id, won, profit)


def get_eod_analysis() -> Dict[str, Any]:
    """Get end-of-day learning analysis"""
    integration = get_hybrid_integration()
    return integration.eod_analysis()


def get_integration_status() -> Dict[str, Any]:
    """Get current integration status"""
    integration = get_hybrid_integration()
    return integration.get_current_status()
