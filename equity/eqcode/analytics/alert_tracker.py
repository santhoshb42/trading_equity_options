"""
Alert Tracker for Analytics

Logs ALL alerts (executed + missed) in structured format for comprehensive analytics.
This is a Phase 1 enhancement that runs safely in background without affecting trading.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from ..config import BASE_DIR
from ..bot_logging import log_event


class AlertTracker:
    """
    Tracks all alerts (executed and missed) for analytics
    
    Fail-safe design: If this breaks, trading continues normally
    """
    
    def __init__(self):
        self.alerts_dir = BASE_DIR / "logs" / datetime.now().strftime('%d-%m-%Y')
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        self.csv_file = self.alerts_dir / "all_alerts.csv"
        
    def log_alert_received(self, alert_data: Dict[str, Any]) -> None:
        """
        Log incoming alert (before any processing)
        
        Args:
            alert_data: Raw alert data from webhook
        """
        try:
            self._write_alert_csv({
                'timestamp': datetime.now().isoformat(),
                'symbol': alert_data.get('symbol', 'UNKNOWN'),
                'action': alert_data.get('action', 'UNKNOWN'), 
                'price': float(alert_data.get('price', 0)),
                'source': 'TradingView',
                'status': 'RECEIVED',
                'execution_status': 'PENDING',
                'rejection_reason': '',
                'capital_used': 0,
                'quantity': 0
            })
        except Exception as e:
            # Never break trading for analytics
            log_event("ANALYTICS_ERROR", f"Failed to log received alert: {str(e)}")
    
    def log_alert_executed(self, alert_data: Dict[str, Any], execution_data: Dict[str, Any]) -> None:
        """
        Log alert that was successfully executed as trade
        
        Args:
            alert_data: Original alert data
            execution_data: Trade execution details
        """
        try:
            self._write_alert_csv({
                'timestamp': datetime.now().isoformat(),
                'symbol': alert_data.get('symbol', 'UNKNOWN'),
                'action': alert_data.get('action', 'UNKNOWN'),
                'price': float(alert_data.get('price', 0)),
                'source': 'TradingView',
                'status': 'EXECUTED',
                'execution_status': 'SUCCESS',
                'rejection_reason': '',
                'capital_used': execution_data.get('capital_used', 0),
                'quantity': execution_data.get('quantity', 0),
                'order_id': execution_data.get('order_id', ''),
                'execution_price': execution_data.get('execution_price', 0)
            })
        except Exception as e:
            log_event("ANALYTICS_ERROR", f"Failed to log executed alert: {str(e)}")
    
    def log_alert_rejected(self, alert_data: Dict[str, Any], rejection_reason: str) -> None:
        """
        Log alert that was rejected (missed opportunity)
        
        Args:
            alert_data: Original alert data
            rejection_reason: Why it was rejected
        """
        try:
            self._write_alert_csv({
                'timestamp': datetime.now().isoformat(),
                'symbol': alert_data.get('symbol', 'UNKNOWN'),
                'action': alert_data.get('action', 'UNKNOWN'),
                'price': float(alert_data.get('price', 0)),
                'source': 'TradingView',
                'status': 'REJECTED',
                'execution_status': 'MISSED',
                'rejection_reason': rejection_reason,
                'capital_used': 0,
                'quantity': 0
            })
            
            # Also log for immediate visibility
            log_event("MISSED_ALERT", f"Alert rejected: {alert_data.get('symbol')} {alert_data.get('action')}", 
                     reason=rejection_reason, price=alert_data.get('price', 0))
        except Exception as e:
            log_event("ANALYTICS_ERROR", f"Failed to log rejected alert: {str(e)}")
    
    def _write_alert_csv(self, alert_record: Dict[str, Any]) -> None:
        """
        Write alert record to CSV file
        
        Args:
            alert_record: Alert data to write
        """
        # Check if file exists to write header
        write_header = not self.csv_file.exists()
        
        # Standard fields for CSV
        fields = [
            'timestamp', 'symbol', 'action', 'price', 'source', 'status', 
            'execution_status', 'rejection_reason', 'capital_used', 'quantity',
            'order_id', 'execution_price'
        ]
        
        # Ensure all fields exist
        for field in fields:
            if field not in alert_record:
                alert_record[field] = ''
        
        # Write to CSV
        with open(self.csv_file, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            
            if write_header:
                writer.writeheader()
            
            writer.writerow({k: alert_record[k] for k in fields})
    
    def get_daily_summary(self, date_str: str = None) -> Dict[str, Any]:
        """
        Get summary of alerts for a specific date
        
        Args:
            date_str: Date in DD-MM-YYYY format (default: today)
            
        Returns:
            Summary statistics
        """
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        csv_file = BASE_DIR / "logs" / date_str / "all_alerts.csv"
        
        if not csv_file.exists():
            return {
                "date": date_str,
                "total_alerts": 0,
                "executed_alerts": 0,
                "missed_alerts": 0,
                "execution_rate": 0,
                "missed_opportunities": []
            }
        
        # Parse CSV and generate summary
        alerts = []
        try:
            with open(csv_file, 'r') as f:
                reader = csv.DictReader(f)
                alerts = list(reader)
        except Exception as e:
            log_event("ANALYTICS_ERROR", f"Failed to read alerts CSV: {str(e)}")
            return {"error": str(e)}
        
        total = len(alerts)
        executed = len([a for a in alerts if a['status'] == 'EXECUTED'])
        missed = len([a for a in alerts if a['status'] == 'REJECTED'])
        
        missed_opportunities = [
            {
                'symbol': a['symbol'],
                'action': a['action'],
                'price': float(a['price']) if a['price'] else 0,
                'reason': a['rejection_reason'],
                'timestamp': a['timestamp']
            }
            for a in alerts if a['status'] == 'REJECTED'
        ]
        
        return {
            "date": date_str,
            "total_alerts": total,
            "executed_alerts": executed,
            "missed_alerts": missed,
            "execution_rate": (executed / total * 100) if total > 0 else 0,
            "missed_opportunities": missed_opportunities,
            "symbols_analysis": self._analyze_symbols(alerts)
        }
    
    def _analyze_symbols(self, alerts: list) -> Dict[str, Any]:
        """Analyze alerts by symbol"""
        symbols = {}
        
        for alert in alerts:
            symbol = alert['symbol']
            if symbol not in symbols:
                symbols[symbol] = {
                    'total_alerts': 0,
                    'executed': 0,
                    'missed': 0,
                    'execution_rate': 0
                }
            
            symbols[symbol]['total_alerts'] += 1
            if alert['status'] == 'EXECUTED':
                symbols[symbol]['executed'] += 1
            elif alert['status'] == 'REJECTED':
                symbols[symbol]['missed'] += 1
        
        # Calculate execution rates
        for symbol_data in symbols.values():
            total = symbol_data['total_alerts']
            executed = symbol_data['executed']
            symbol_data['execution_rate'] = (executed / total * 100) if total > 0 else 0
        
        return symbols


# Global instance for easy import
alert_tracker = AlertTracker()


def track_alert_received(alert_data: Dict[str, Any]) -> None:
    """Convenience function to track received alert"""
    alert_tracker.log_alert_received(alert_data)


def track_alert_executed(alert_data: Dict[str, Any], execution_data: Dict[str, Any]) -> None:
    """Convenience function to track executed alert"""
    alert_tracker.log_alert_executed(alert_data, execution_data)


def track_alert_rejected(alert_data: Dict[str, Any], rejection_reason: str) -> None:
    """Convenience function to track rejected alert"""
    alert_tracker.log_alert_rejected(alert_data, rejection_reason)


def get_alert_summary(date_str: str = None) -> Dict[str, Any]:
    """Convenience function to get alert summary"""
    return alert_tracker.get_daily_summary(date_str)


def get_comprehensive_alert_summary(days_back: int = 7) -> Dict[str, Any]:
    """Get comprehensive alert tracking summary for multiple days"""
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        total_alerts = 0
        executed_alerts = 0
        missed_alerts = 0
        symbols_data = {}
        daily_data = {}
        
        # Collect data for each day
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            day_summary = alert_tracker.get_daily_summary(date_str)
            
            if day_summary:
                daily_data[date_str] = day_summary
                total_alerts += day_summary.get('total_alerts', 0)
                executed_alerts += day_summary.get('executed_alerts', 0)
                missed_alerts += day_summary.get('missed_alerts', 0)
                
                # Aggregate symbols data
                for symbol, data in day_summary.get('by_symbol', {}).items():
                    if symbol not in symbols_data:
                        symbols_data[symbol] = {
                            'total_alerts': 0,
                            'executed': 0,
                            'missed': 0,
                            'execution_rate': 0
                        }
                    
                    symbols_data[symbol]['total_alerts'] += data.get('total_alerts', 0)
                    symbols_data[symbol]['executed'] += data.get('executed', 0)
                    symbols_data[symbol]['missed'] += data.get('missed', 0)
            
            current_date += timedelta(days=1)
        
        # Calculate execution rates
        for symbol_data in symbols_data.values():
            total = symbol_data['total_alerts']
            executed = symbol_data['executed']
            symbol_data['execution_rate'] = (executed / total * 100) if total > 0 else 0
        
        # Calculate overall execution rate
        overall_execution_rate = (executed_alerts / total_alerts * 100) if total_alerts > 0 else 0
        
        return {
            'analysis_date': datetime.now().isoformat(),
            'period_days': days_back,
            'date_range': {
                'start': start_date.strftime('%d-%m-%Y'),  # DD-MM-YYYY: AngelOne format
                'end': end_date.strftime('%d-%m-%Y')  # DD-MM-YYYY: AngelOne format
            },
            'summary': {
                'total_alerts': total_alerts,
                'executed_alerts': executed_alerts,
                'missed_alerts': missed_alerts,
                'overall_execution_rate': round(overall_execution_rate, 2)
            },
            'by_symbol': symbols_data,
            'daily_breakdown': daily_data
        }
        
    except Exception as e:
        return {
            'error': f'Failed to get alert summary: {str(e)}',
            'analysis_date': datetime.now().isoformat(),
            'period_days': days_back
        }