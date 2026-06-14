"""
Analytics API Endpoints for 5% Target Tracking

FastAPI endpoints to monitor and optimize 5% daily growth target
"""

from fastapi import APIRouter
from typing import Dict, List
import asyncio
from datetime import datetime

from .target_analytics import TargetAnalytics
from ..bot_logging import log_event

router = APIRouter()
analytics = TargetAnalytics()


@router.get("/target-dashboard")
async def get_target_dashboard() -> Dict:
    """Get comprehensive dashboard for 5% daily target tracking"""
    try:
        dashboard = analytics.get_daily_dashboard()
        
        # Add real-time calculations
        current_time = datetime.now()
        market_hours_elapsed = max(0, (current_time.hour - 9) + (current_time.minute / 60))
        market_hours_remaining = max(0, 6.5 - market_hours_elapsed)  # 9:15 AM to 3:30 PM
        
        dashboard['timing'] = {
            'current_time': current_time.strftime('%H:%M:%S'),
            'market_hours_elapsed': round(market_hours_elapsed, 1),
            'market_hours_remaining': round(market_hours_remaining, 1),
            'pace_required': dashboard['target']['daily_target_amount'] / max(market_hours_remaining, 0.1) if market_hours_remaining > 0 else 0
        }
        
        # Add urgency indicators
        achievement_percent = dashboard['performance']['achievement_percent']
        time_percent = (market_hours_elapsed / 6.5) * 100
        
        dashboard['urgency'] = {
            'behind_schedule': achievement_percent < time_percent * 0.8,
            'on_track': time_percent * 0.8 <= achievement_percent <= time_percent * 1.2,
            'ahead_of_schedule': achievement_percent > time_percent * 1.2,
            'critical_time': market_hours_remaining < 2 and achievement_percent < 70
        }
        
        return dashboard
        
    except Exception as e:
        log_event("ERROR", f"Error getting target dashboard: {str(e)}")
        return {"error": str(e)}


@router.get("/target-recommendations")
async def get_target_recommendations() -> Dict:
    """Get AI-driven recommendations for achieving 5% target"""
    try:
        recommendations = analytics.get_recommendations()
        
        # Add real-time strategy adjustments
        dashboard = analytics.get_daily_dashboard()
        achievement_percent = dashboard['performance']['achievement_percent']
        trades_completed = dashboard['performance']['trades_completed']
        
        # Dynamic strategy suggestions
        if achievement_percent < 30 and trades_completed >= 3:
            recommendations['optimization_suggestions'].append(
                "Consider increasing position size within risk limits or switching to higher volatility stocks"
            )
        
        if trades_completed < 2 and datetime.now().hour >= 14:  # After 2 PM
            recommendations['optimization_suggestions'].append(
                "Time pressure: Focus on quick scalping opportunities with 0.5-1% targets"
            )
        
        return recommendations
        
    except Exception as e:
        log_event("ERROR", f"Error getting recommendations: {str(e)}")
        return {"error": str(e)}


@router.post("/track-trade")
async def track_trade_completion(trade_data: Dict) -> Dict:
    """Track a completed trade and analyze against 5% target"""
    try:
        analysis = analytics.track_trade_completion(trade_data)
        
        # Add immediate next-action suggestions
        if analysis.get('target_achieved'):
            analysis['next_action'] = "Excellent! Continue with similar strategy for remaining positions"
        else:
            profit_percent = analysis.get('profit_percent', 0)
            if profit_percent > 0:
                analysis['next_action'] = f"Positive but below target. Consider tighter entry criteria"
            else:
                analysis['next_action'] = "Loss incurred. Review entry strategy and risk management"
        
        return analysis
        
    except Exception as e:
        log_event("ERROR", f"Error tracking trade: {str(e)}")
        return {"error": str(e)}


@router.get("/symbol-performance/{symbol}")
async def get_symbol_performance(symbol: str) -> Dict:
    """Get detailed performance analysis for a specific symbol"""
    try:
        import sqlite3
        
        with sqlite3.connect(analytics.db_file) as conn:
            # Symbol stats
            cursor = conn.execute("""
                SELECT * FROM symbol_performance WHERE symbol = ?
            """, (symbol,))
            symbol_data = cursor.fetchone()
            
            if not symbol_data:
                return {"error": f"No data found for symbol {symbol}"}
            
            # Recent trades for this symbol
            cursor = conn.execute("""
                SELECT date, profit_percent, target_achieved, hold_duration_minutes, exit_reason
                FROM trade_efficiency 
                WHERE symbol = ? 
                ORDER BY exit_time DESC 
                LIMIT 10
            """, (symbol,))
            recent_trades = cursor.fetchall()
            
            # Performance trends (last 7 days)
            cursor = conn.execute("""
                SELECT date, AVG(profit_percent) as avg_profit, 
                       COUNT(CASE WHEN target_achieved = 1 THEN 1 END) as successful_trades,
                       COUNT(*) as total_trades
                FROM trade_efficiency 
                WHERE symbol = ? AND date >= date('now', '-7 days')
                GROUP BY date
                ORDER BY date DESC
            """, (symbol,))
            trends = cursor.fetchall()
        
        performance = {
            'symbol': symbol,
            'overall_stats': {
                'total_trades': symbol_data[1],
                'profitable_trades': symbol_data[2],
                'win_rate': symbol_data[3],
                'avg_profit_percent': symbol_data[4],
                'avg_hold_duration': symbol_data[5],
                'best_profit': symbol_data[6],
                'worst_profit': symbol_data[7],
                'target_achievement_rate': symbol_data[8]
            },
            'recent_trades': [
                {
                    'date': row[0],
                    'profit_percent': row[1],
                    'target_achieved': bool(row[2]),
                    'hold_duration_minutes': row[3],
                    'exit_reason': row[4]
                }
                for row in recent_trades
            ],
            'weekly_trends': [
                {
                    'date': row[0],
                    'avg_profit': row[1],
                    'successful_trades': row[2],
                    'total_trades': row[3],
                    'success_rate': (row[2] / row[3] * 100) if row[3] > 0 else 0
                }
                for row in trends
            ]
        }
        
        # Add recommendation for this symbol
        target_achievement_rate = symbol_data[8]
        avg_profit = symbol_data[4]
        
        if target_achievement_rate >= 70 and avg_profit >= 1:
            performance['recommendation'] = "HIGH PRIORITY - Excellent performance, prioritize this symbol"
        elif target_achievement_rate >= 50:
            performance['recommendation'] = "MEDIUM PRIORITY - Good potential, monitor closely"
        else:
            performance['recommendation'] = "LOW PRIORITY - Consider avoiding or reducing position size"
        
        return performance
        
    except Exception as e:
        log_event("ERROR", f"Error getting symbol performance: {str(e)}")
        return {"error": str(e)}


@router.get("/margin-utilization")
async def get_margin_utilization() -> Dict:
    """Get real-time margin utilization analysis"""
    try:
        import sqlite3
        
        with sqlite3.connect(analytics.db_file) as conn:
            # Get latest portfolio state
            cursor = conn.execute("""
                SELECT * FROM portfolio_utilization 
                ORDER BY timestamp DESC LIMIT 1
            """)
            portfolio = cursor.fetchone()
            
            # Calculate optimal allocation
            total_capital = analytics.total_capital
            margin_per_trade = analytics.capital_per_trade
            max_positions = analytics.max_positions
            
            current_positions = portfolio[1] if portfolio else 0
            margin_utilized = portfolio[2] if portfolio else 0
            
            utilization = {
                'capital_allocation': {
                    'total_capital': total_capital,
                    'margin_per_trade': margin_per_trade,
                    'max_positions': max_positions,
                    'max_possible_margin': margin_per_trade * max_positions
                },
                'current_state': {
                    'active_positions': current_positions,
                    'margin_utilized': margin_utilized,
                    'available_slots': max_positions - current_positions,
                    'available_margin': (max_positions - current_positions) * margin_per_trade,
                    'utilization_percent': (current_positions / max_positions) * 100
                },
                'optimization': {
                    'underutilized': current_positions < max_positions * 0.8,
                    'fully_utilized': current_positions == max_positions,
                    'can_add_positions': current_positions < max_positions,
                    'recommended_action': None
                }
            }
            
            # Add recommendations
            if current_positions < max_positions * 0.5:
                utilization['optimization']['recommended_action'] = "SCALE UP - Only using 50% of capacity"
            elif current_positions < max_positions:
                utilization['optimization']['recommended_action'] = f"ADD POSITIONS - {max_positions - current_positions} slots available"
            else:
                utilization['optimization']['recommended_action'] = "FULLY ALLOCATED - Monitor for exit opportunities"
            
            return utilization
            
    except Exception as e:
        log_event("ERROR", f"Error getting margin utilization: {str(e)}")
        return {"error": str(e)}


@router.get("/target-progress")
async def get_target_progress() -> Dict:
    """Get real-time progress toward 5% daily target"""
    try:
        dashboard = analytics.get_daily_dashboard()
        
        target_amount = dashboard['target']['daily_target_amount']
        achieved_amount = dashboard['performance']['achieved_amount']
        achievement_percent = dashboard['performance']['achievement_percent']
        
        # Calculate required performance for remaining time
        current_hour = datetime.now().hour
        current_minute = datetime.now().minute
        
        if 9 <= current_hour <= 15:  # Market hours
            elapsed_minutes = (current_hour - 9) * 60 + current_minute
            remaining_minutes = (15 * 60 + 30) - elapsed_minutes  # Until 3:30 PM
            
            if remaining_minutes > 0:
                required_rate = (target_amount - achieved_amount) / (remaining_minutes / 60)
            else:
                required_rate = 0
        else:
            remaining_minutes = 0
            required_rate = 0
        
        progress = {
            'target': {
                'amount': target_amount,
                'percentage': 5.0
            },
            'current': {
                'achieved_amount': achieved_amount,
                'achievement_percent': achievement_percent,
                'remaining_amount': target_amount - achieved_amount
            },
            'timing': {
                'elapsed_minutes': elapsed_minutes if 9 <= current_hour <= 15 else 0,
                'remaining_minutes': max(0, remaining_minutes),
                'required_hourly_rate': required_rate,
                'pace_status': 'on_track' if achievement_percent >= 60 else 'behind' if achievement_percent >= 30 else 'critical'
            },
            'milestones': {
                '25_percent_target': target_amount * 0.25,
                '50_percent_target': target_amount * 0.50,
                '75_percent_target': target_amount * 0.75,
                '25_percent_achieved': achieved_amount >= target_amount * 0.25,
                '50_percent_achieved': achieved_amount >= target_amount * 0.50,
                '75_percent_achieved': achieved_amount >= target_amount * 0.75,
                'target_achieved': achieved_amount >= target_amount
            }
        }
        
        return progress
        
    except Exception as e:
        log_event("ERROR", f"Error getting target progress: {str(e)}")
        return {"error": str(e)}