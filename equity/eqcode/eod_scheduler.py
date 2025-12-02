"""
EOD Scheduler - End-of-Day Learning Update

Multi-stage EOD process:
1. 3:12 PM - Square off all trades (avoid EOD rush)
2. 3:15 PM - Trigger learning update for missed trades
3. 3:30 PM - Market closes

Timing strategy:
- 3:12 PM: Square off captures clean exits before EOD pressure
- 3:15 PM: Paper trading gets more complete data (15 min before close)
- 3:30 PM: Market close (learning ingestion complete)

Can be run as:
1. Background service/daemon
2. Cron job (3:12 PM & 3:15 PM daily)
3. Manual trigger via API endpoint

Usage:
    # As background process
    python eqcode/eod_scheduler.py
    
    # Via cron (add to crontab)
    12 15 * * 1-5 /usr/bin/python3 /path/to/square_off.py
    15 15 * * 1-5 /usr/bin/python3 /path/to/eqcode/eod_scheduler.py
    
    # Via API
    curl -X POST http://localhost:5000/square-off
    curl -X POST http://localhost:5000/learning/eod-update
"""

import requests
import time
import schedule
from datetime import datetime
from typing import Dict, Any, Optional

from .config import WebhookConfig
from .bot_logging import log_event, log_error


class EODScheduler:
    """Manages end-of-day learning updates with robust missed-trigger handling"""
    
    def __init__(self, bot_url: str = None, squareoff_time: str = "15:12", 
                 learning_time: str = "15:15"):
        """
        Initialize EOD scheduler with multi-stage timing
        
        Args:
            bot_url: URL of the bot API (default: http://localhost:5000)
            squareoff_time: Time to square off all trades (default: 15:12 = 3:12 PM)
            learning_time: Time to run learning update (default: 15:15 = 3:15 PM)
        
        Timing Strategy:
            3:12 PM - Square off all trades (before EOD rush)
            3:15 PM - Run EOD learning for missed trades (15 min before close)
            3:30 PM - Market closes (all data collected)
        """
        self.bot_url = bot_url or "http://localhost:5000"
        self.squareoff_time = squareoff_time
        self.learning_time = learning_time
        self.running = False
        self.last_squareoff = None
        self.last_update = None
        self.last_update_date = None  # Track which date we last updated
        self.update_count = 0
        self.squareoff_count = 0
        self.trigger_time = datetime.now().isoformat()  # Initialize trigger_time for logging
        
        # Parse square-off time
        try:
            squareoff_parts = squareoff_time.split(':')
            self.squareoff_hour = int(squareoff_parts[0])
            self.squareoff_minute = int(squareoff_parts[1])
        except (ValueError, IndexError):
            log_error("EOD_CONFIG_ERROR", f"Invalid squareoff_time format: {squareoff_time}")
            self.squareoff_hour = 15
            self.squareoff_minute = 12
        
        # Parse learning trigger time
        try:
            learning_parts = learning_time.split(':')
            self.learning_hour = int(learning_parts[0])
            self.learning_minute = int(learning_parts[1])
        except (ValueError, IndexError):
            log_error("EOD_CONFIG_ERROR", f"Invalid learning_time format: {learning_time}")
            self.learning_hour = 15
            self.learning_minute = 15
    
    def trigger_squareoff(self) -> Dict[str, Any]:
        """
        Trigger square-off of all open positions at 3:12 PM
        
        This runs BEFORE learning update to:
        1. Close all positions before EOD rush
        2. Avoid market volatility at 3:30 PM
        3. Get clean exits for paper trading comparison
        
        Returns:
            Response from the API
        """
        try:
            log_event("EOD_SQUAREOFF", f"Triggering position square-off at {datetime.now().isoformat()}")
            
            response = requests.post(
                f"{self.bot_url}/square-off",
                json={"reason": "EOD_SQUAREOFF"},
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code == 200:
                self.last_squareoff = datetime.now()
                self.squareoff_count += 1
                
                log_event("EOD_SQUAREOFF_SUCCESS", 
                         f"Position square-off completed successfully",
                         squareoff_count=self.squareoff_count,
                         timestamp=self.last_squareoff.isoformat(),
                         details=result)
                
                return {
                    "status": "success",
                    "message": "Position square-off completed",
                    "timestamp": self.last_squareoff.isoformat(),
                    "squareoff_count": self.squareoff_count,
                    "response": result
                }
            else:
                log_error("EOD_SQUAREOFF_FAILED", 
                         f"Square-off failed with status {response.status_code}",
                         Exception(result.get('error', 'Unknown error')),
                         context={"response": result})
                
                return {
                    "status": "failed",
                    "message": f"Square-off failed: {result.get('error', 'Unknown error')}",
                    "timestamp": datetime.now().isoformat(),
                    "response": result
                }
        
        except requests.exceptions.ConnectionError as e:
            log_error("EOD_SQUAREOFF_CONNECTION_ERROR", 
                     f"Failed to connect to bot at {self.bot_url}",
                     e)
            return {
                "status": "error",
                "message": f"Connection failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
        
        except Exception as e:
            log_error("EOD_SQUAREOFF_ERROR", 
                     "Square-off scheduler encountered an error",
                     e)
            return {
                "status": "error",
                "message": f"Scheduler error: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
    
    def trigger_eod_update(self) -> Dict[str, Any]:
        """
        Trigger end-of-day learning update via API at 3:15 PM
        
        This runs AFTER square-off (3:15 PM) to:
        1. Process all missed trades from the day
        2. Fetch EOD LTP with 15 minutes buffer before close
        3. Simulate paper trades with fresh data
        4. Update learning model
        5. Complete before market close (3:30 PM)
        
        Returns:
            Response from the API
        """
        try:
            log_event("EOD_SCHEDULER", f"Triggering EOD learning update at {datetime.now().isoformat()}")
            
            response = requests.post(
                f"{self.bot_url}/learning/eod-update",
                json={},
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code == 200:
                self.last_update = datetime.now()
                self.last_update_date = self.last_update.date()  # Track update date
                self.update_count += 1
                
                log_event("EOD_UPDATE_SUCCESS", 
                         f"EOD learning update completed successfully",
                         update_count=self.update_count,
                         timestamp=self.last_update.isoformat())
                
                return {
                    "status": "success",
                    "message": "EOD learning update completed",
                    "timestamp": self.last_update.isoformat(),
                    "update_count": self.update_count,
                    "response": result
                }
            else:
                log_error("EOD_UPDATE_FAILED", 
                         f"EOD learning update failed with status {response.status_code}",
                         Exception(result.get('error', 'Unknown error')),
                         context={"response": result})
                
                return {
                    "status": "failed",
                    "message": f"EOD update failed: {result.get('error', 'Unknown error')}",
                    "timestamp": datetime.now().isoformat(),
                    "response": result
                }
        
        except requests.exceptions.ConnectionError as e:
            log_error("EOD_CONNECTION_ERROR", 
                     f"Failed to connect to bot at {self.bot_url}",
                     e)
            return {
                "status": "error",
                "message": f"Connection failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
        
        except Exception as e:
            log_error("EOD_SCHEDULER_ERROR", 
                     "EOD scheduler encountered an error",
                     e)
            return {
                "status": "error",
                "message": f"Scheduler error: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
    
    def schedule_daily(self):
        """Schedule EOD process to run daily in two stages"""
        # Stage 1: Square off at 3:12 PM
        schedule.every().day.at(self.squareoff_time).do(self.trigger_squareoff)
        
        # Stage 2: Learning update at 3:15 PM
        schedule.every().day.at(self.learning_time).do(self.trigger_eod_update)
        
        log_event("EOD_SCHEDULER_SCHEDULED", 
                 f"EOD scheduler scheduled with two stages",
                 squareoff_time=self.squareoff_time,
                 learning_time=self.learning_time,
                 description="Stage 1: Square-off at 3:12 PM (avoid EOD rush) | Stage 2: Learning at 3:15 PM (get more data)")
    
    def should_run_eod_update(self) -> bool:
        """
        Check if EOD update should run
        
        This handles:
        1. Scheduled time (15:30 daily)
        2. Missed triggers (if bot starts after 15:30)
        3. Prevents double execution in same day
        
        Returns:
            True if EOD update should execute
        """
        from datetime import date
        
        current_time = datetime.now()
        current_date = current_time.date()
        
        # Check if we already updated today
        if self.last_update_date == current_date:
            return False  # Already ran today
        
        # Check if current time is >= trigger time (use learning time as trigger)
        if (current_time.hour > self.learning_hour or 
            (current_time.hour == self.learning_hour and current_time.minute >= self.learning_minute)):
            return True  # Time has passed, run now to catch missed trigger
        
        return False
    
    def run_eod_update_if_needed(self):
        """
        Run EOD update if conditions are met
        Handles missed triggers from late bot starts
        """
        if self.should_run_eod_update():
            log_event("EOD_SCHEDULER", 
                     "EOD update triggered (may be a missed trigger from late bot start)",
                     trigger_time=self.trigger_time)
            self.trigger_eod_update()
    
    def schedule_every_n_hours(self, hours: int = 1):
        """Schedule EOD update to run every N hours (for testing)"""
        schedule.every(hours).hours.do(self.trigger_eod_update)
        
        log_event("EOD_SCHEDULER_SCHEDULED", 
                 f"EOD update scheduled every {hours} hour(s)",
                 interval_hours=hours)
    
    def run_continuous(self):
        """
        Run scheduler continuously (blocks until stopped)
        Use in a background thread or process
        
        Enhanced to catch missed triggers from late bot starts
        """
        self.running = True
        
        log_event("EOD_SCHEDULER_STARTED", 
                 f"EOD scheduler started, squareoff: {self.squareoff_time}, learning: {self.learning_time}")
        
        # Immediately check if this is a late start (after 15:30)
        if self.should_run_eod_update():
            log_event("EOD_SCHEDULER", 
                     "Bot started after trigger time - attempting missed EOD update")
            self.trigger_eod_update()
        
        try:
            while self.running:
                # Check scheduled tasks
                schedule.run_pending()
                
                # Also check for missed triggers (in case schedule library misses it)
                self.run_eod_update_if_needed()
                
                time.sleep(60)  # Check every minute if a task is pending
        
        except KeyboardInterrupt:
            log_event("EOD_SCHEDULER_STOPPED", "EOD scheduler stopped by user")
            self.running = False
        
        except Exception as e:
            log_error("EOD_SCHEDULER_CRASHED", 
                     "EOD scheduler crashed",
                     e)
            self.running = False
    
    def stop(self):
        """Stop the scheduler"""
        self.running = False
        log_event("EOD_SCHEDULER_STOP_REQUESTED", "EOD scheduler stop requested")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status"""
        return {
            "running": self.running,
            "squareoff_time": self.squareoff_time,
            "learning_time": self.learning_time,
            "bot_url": self.bot_url,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "update_count": self.update_count,
            "timestamp": datetime.now().isoformat()
        }


# Global scheduler instance
_scheduler_instance: Optional[EODScheduler] = None


def get_eod_scheduler(bot_url: str = None, squareoff_time: str = "15:12", 
                      learning_time: str = "15:15") -> EODScheduler:
    """Get or create EOD scheduler instance"""
    global _scheduler_instance
    
    if _scheduler_instance is None:
        _scheduler_instance = EODScheduler(bot_url, squareoff_time, learning_time)
    
    return _scheduler_instance


def start_eod_scheduler_daemon(bot_url: str = None, trigger_time: str = "15:30"):
    """
    Start EOD scheduler as a background daemon thread
    
    Args:
        bot_url: URL of the bot API
        trigger_time: Deprecated - kept for backward compatibility. Use squareoff_time and learning_time instead.
                     The scheduler will run at 3:12 PM (squareoff) and 3:15 PM (learning) by default.
    """
    import threading
    
    # Fixed timing: squareoff at 15:12 (3:12 PM), learning at 15:15 (3:15 PM)
    # Note: trigger_time parameter is deprecated and ignored; proper times are now always 15:12 and 15:15
    scheduler = get_eod_scheduler(bot_url, squareoff_time="15:12", learning_time="15:15")
    scheduler.schedule_daily()
    
    # Start in background thread
    thread = threading.Thread(target=scheduler.run_continuous, daemon=True)
    thread.start()
    
    log_event("EOD_DAEMON_STARTED", 
             f"EOD scheduler started as daemon thread (square-off at 15:12, learning at 15:15)",
             squareoff_time="15:12",
             learning_time="15:15")
    
    return scheduler


# =============================================================================
# Command-line interface
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="EOD Scheduler for Hybrid Learning")
    parser.add_argument("--bot-url", default="http://localhost:5000",
                       help="Bot API URL (default: http://localhost:5000)")
    parser.add_argument("--trigger-time", default="15:30",
                       help="Trigger time in HH:MM format (default: 15:30)")
    parser.add_argument("--test", action="store_true",
                       help="Run test mode (trigger every minute)")
    parser.add_argument("--trigger-now", action="store_true",
                       help="Trigger EOD update immediately and exit")
    
    args = parser.parse_args()
    
    # Create scheduler
    scheduler = get_eod_scheduler(args.bot_url, args.trigger_time)
    
    if args.trigger_now:
        # Trigger immediately
        print("Triggering EOD learning update immediately...")
        result = scheduler.trigger_eod_update()
        print(f"Result: {result}")
    
    elif args.test:
        # Test mode: trigger every minute
        print(f"Test mode: Scheduling updates every minute")
        scheduler.schedule_every_n_hours(hours=0)  # Every minute
        scheduler.run_continuous()
    
    else:
        # Normal mode: daily at trigger_time
        print(f"Starting EOD scheduler (daily at {args.trigger_time})")
        print(f"Bot URL: {args.bot_url}")
        scheduler.schedule_daily()
        scheduler.run_continuous()
