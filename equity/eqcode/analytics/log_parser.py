"""
Log Parser for Analytics

Parses trading logs to extract completed trades and feed into analytics system.
Useful for:
- Post-session analysis after market closes
- Backfilling analytics data from historical logs
- Recovery when real-time tracking missed trades
"""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import BASE_DIR
from ..bot_logging import log_event
from .target_analytics import TargetAnalytics


class LogParser:
    """
    Parses trading logs to extract trade data for analytics
    """
    
    def __init__(self):
        self.logs_dir = BASE_DIR / "logs"
        self.analytics = TargetAnalytics()
    
    def parse_daily_logs(self, date_str: str = None) -> Dict:
        """
        Parse logs for a specific date and extract all completed trades
        
        Args:
            date_str: Date in DD-MM-YYYY format (default: today)
            
        Returns:
            Summary of parsed trades and analytics results
        """
        if not date_str:
            date_str = datetime.now().strftime('%d-%m-%Y')  # DD-MM-YYYY: AngelOne format
        
        log_event("LOG_PARSER", f"Parsing logs for {date_str}")
        
        # Find log directory for this date
        date_dir = self.logs_dir / date_str
        if not date_dir.exists():
            return {
                "error": f"No log directory found for {date_str}",
                "parsed_trades": 0
            }
        
        results = {
            "date": date_str,
            "parsed_trades": 0,
            "trade_analyses": [],
            "errors": []
        }
        
        # Parse trades.csv
        trades_file = date_dir / "trades.csv"
        if trades_file.exists():
            completed_trades = self._parse_trades_csv(trades_file)
            results["parsed_trades"] = len(completed_trades)
            
            # Process each completed trade through analytics
            for trade in completed_trades:
                try:
                    analysis = self.analytics.track_trade_completion(trade)
                    results["trade_analyses"].append({
                        "symbol": trade["symbol"],
                        "analysis": analysis
                    })
                except Exception as e:
                    results["errors"].append(f"Error analyzing {trade.get('symbol', 'unknown')}: {str(e)}")
        else:
            results["errors"].append(f"trades.csv not found in {date_dir}")
        
        # Parse alerts.log for additional context
        alerts_file = date_dir / "alerts.log"
        if alerts_file.exists():
            alert_count = self._parse_alerts_log(alerts_file)
            results["alerts_processed"] = alert_count
        
        log_event("LOG_PARSER", f"Completed parsing {date_str}", **results)
        return results
    
    def _parse_trades_csv(self, trades_file: Path) -> List[Dict]:
        """
        Parse trades.csv to extract completed trades
        
        Returns:
            List of completed trade dictionaries ready for analytics
        """
        completed_trades = []
        
        try:
            with open(trades_file, 'r') as f:
                reader = csv.DictReader(f)
                
                # Group trades by symbol to match entry/exit pairs
                open_positions = {}
                
                for row in reader:
                    symbol = row['symbol']
                    action = row['action']
                    status = row['status']
                    
                    if action == 'BUY' and status == 'OPEN':
                        # Store entry position
                        open_positions[symbol] = {
                            'symbol': symbol,
                            'entry_price': float(row['entry_price']),
                            'quantity': int(row['quantity']),
                            'margin_used': float(row['capital_used']),
                            'entry_time': f"{row['date']} {row['time']}",
                            'sl_price': float(row['sl_price']) if row['sl_price'] else 0
                        }
                    
                    elif action == 'SELL' and status == 'CLOSED':
                        # Find matching entry and create completed trade
                        if symbol in open_positions:
                            entry = open_positions[symbol]
                            
                            # Convert date format if needed (DD-MM-YYYY HH:MM:SS)
                            entry_time = self._parse_datetime(entry['entry_time'])
                            exit_time = self._parse_datetime(f"{row['date']} {row['time']}")
                            
                            completed_trade = {
                                'symbol': symbol,
                                'entry_price': entry['entry_price'],
                                'exit_price': float(row['exit_price']),
                                'quantity': entry['quantity'],
                                'margin_used': entry['margin_used'],
                                'entry_time': entry_time.isoformat(),
                                'exit_time': exit_time.isoformat(),
                                'exit_reason': self._determine_exit_reason(
                                    entry['entry_price'], 
                                    float(row['exit_price']), 
                                    entry.get('sl_price', 0),
                                    float(row.get('pnl', 0))
                                )
                            }
                            
                            completed_trades.append(completed_trade)
                            
                            # Remove from open positions
                            del open_positions[symbol]
                            
                            log_event("TRADE_PARSED", f"Completed trade: {symbol}", 
                                    entry_price=entry['entry_price'],
                                    exit_price=float(row['exit_price']),
                                    pnl=float(row.get('pnl', 0)))
        
        except Exception as e:
            log_event("ERROR", f"Error parsing trades.csv: {str(e)}")
        
        return completed_trades
    
    def _parse_alerts_log(self, alerts_file: Path) -> int:
        """Parse alerts.log to count processed alerts"""
        alert_count = 0
        
        try:
            with open(alerts_file, 'r') as f:
                for line in f:
                    if 'ALERT:' in line:
                        alert_count += 1
        except Exception as e:
            log_event("ERROR", f"Error parsing alerts.log: {str(e)}")
        
        return alert_count
    
    def _parse_datetime(self, datetime_str: str) -> datetime:
        """Parse datetime string in various formats"""
        # Try different formats
        formats = [
            '%d-%m-%Y %H:%M:%S',    # DD-MM-YYYY HH:MM:SS (AngelOne format)
            '%d/%m/%Y %H:%M:%S',    # DD/MM/YYYY HH:MM:SS
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(datetime_str, fmt)
            except ValueError:
                continue
        
        # Fallback: assume current date with time only
        time_part = datetime_str.split(' ')[-1]
        today = datetime.now().strftime('%d-%m-%Y')  # DD-MM-YYYY: AngelOne format
        return datetime.strptime(f"{today} {time_part}", '%Y-%m-%d %H:%M:%S')
    
    def _determine_exit_reason(self, entry_price: float, exit_price: float, 
                              sl_price: float, pnl: float) -> str:
        """Determine why a trade was exited"""
        profit_percent = ((exit_price - entry_price) / entry_price) * 100
        
        if pnl > 0:
            if profit_percent >= 1.0:  # Target achieved
                return "TARGET_ACHIEVED"
            else:
                return "PARTIAL_PROFIT"
        elif sl_price > 0 and exit_price <= sl_price * 1.01:  # Within 1% of SL
            return "STOP_LOSS"
        else:
            return "MANUAL_EXIT"
    
    def backfill_analytics(self, start_date: str, end_date: str = None) -> Dict:
        """
        Backfill analytics data from historical logs
        
        Args:
            start_date: Start date in DD-MM-YYYY format
            end_date: End date in DD-MM-YYYY format (default: today)
            
        Returns:
            Summary of backfill operation
        """
        if not end_date:
            end_date = datetime.now().strftime('%d-%m-%Y')  # DD-MM-YYYY: AngelOne format
        
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        
        results = {
            "start_date": start_date,
            "end_date": end_date,
            "dates_processed": 0,
            "total_trades": 0,
            "errors": []
        }
        
        current_dt = start_dt
        while current_dt <= end_dt:
            date_str = current_dt.strftime('%d-%m-%Y')  # DD-MM-YYYY: AngelOne format
            
            try:
                daily_result = self.parse_daily_logs(date_str)
                if "error" not in daily_result:
                    results["dates_processed"] += 1
                    results["total_trades"] += daily_result["parsed_trades"]
                else:
                    results["errors"].append(daily_result["error"])
            except Exception as e:
                results["errors"].append(f"Error processing {date_str}: {str(e)}")
            
            current_dt += timedelta(days=1)
        
        log_event("BACKFILL_COMPLETE", f"Processed {results['dates_processed']} dates", **results)
        return results
    
    def get_missing_analytics_dates(self) -> List[str]:
        """
        Find dates with logs but no analytics data
        
        Returns:
            List of dates in DD-MM-YYYY format that need processing
        """
        missing_dates = []
        
        # Get all log directories
        if not self.logs_dir.exists():
            return missing_dates
        
        for date_dir in self.logs_dir.iterdir():
            if date_dir.is_dir() and self._is_valid_date_format(date_dir.name):
                # Check if analytics exist for this date
                if not self._has_analytics_data(date_dir.name):
                    missing_dates.append(date_dir.name)
        
        return sorted(missing_dates)
    
    def _is_valid_date_format(self, date_str: str) -> bool:
        """Check if string is a valid YYYY-MM-DD date"""
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except ValueError:
            return False
    
    def _has_analytics_data(self, date_str: str) -> bool:
        """Check if analytics data exists for a date"""
        try:
            import sqlite3
            
            with sqlite3.connect(self.analytics.db_file) as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM daily_performance WHERE date = ?",
                    (date_str,)
                )
                count = cursor.fetchone()[0]
                return count > 0
        except Exception:
            return False


def run_post_session_analysis(date_str: str = None) -> Dict:
    """
    Run complete post-session analysis for a trading day
    
    Args:
        date_str: Date in DD-MM-YYYY format (default: today)
        
    Returns:
        Complete analysis results
    """
    parser = LogParser()
    
    # Parse logs and extract trades
    parse_results = parser.parse_daily_logs(date_str)
    
    if parse_results.get("parsed_trades", 0) > 0:
        # Get updated analytics dashboard
        analytics = TargetAnalytics()
        dashboard = analytics.get_daily_dashboard()
        recommendations = analytics.get_recommendations()
        
        return {
            "log_parsing": parse_results,
            "daily_performance": dashboard,
            "recommendations": recommendations,
            "session_complete": True
        }
    else:
        return {
            "log_parsing": parse_results,
            "session_complete": False,
            "message": "No completed trades found in logs"
        }


if __name__ == "__main__":
    # Test log parser
    parser = LogParser()
    
    # Check for missing analytics dates
    missing = parser.get_missing_analytics_dates()
    if missing:
        print(f"Found {len(missing)} dates with logs but no analytics:")
        for date in missing[:5]:  # Show first 5
            print(f"  - {date}")
        
        # Optionally backfill
        if input("Backfill analytics? (y/n): ").lower() == 'y':
            result = parser.backfill_analytics(missing[0], missing[-1])
            print(f"Backfilled {result['total_trades']} trades across {result['dates_processed']} dates")
    else:
        print("All log dates have analytics data")
    
    # Run today's analysis
    today_analysis = run_post_session_analysis()
    print(f"Today's analysis: {today_analysis['session_complete']}")