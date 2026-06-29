#!/usr/bin/env python3
"""
End of Day (EOD) Backup Handler

Backs up live trading data at end of day and clears files for next day.
Called automatically at market close (3:30 PM) or manually via cron/bot.

Features:
- Backs up live_data.json
- Backs up live_data_tables.md
- Backs up live_data_trades.csv
- Clears live data for next trading day
- Creates timestamped archive files
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

try:
    from optcode.optconfig import build_empty_live_data
except ImportError:
    from options.optcode.optconfig import build_empty_live_data

# =============================================================================
# Configuration
# =============================================================================

DATA_DIR = Path('/root/santhosh/trading/options/data')
ARCHIVE_DIR = DATA_DIR / 'archive'
BACKUP_DIR = DATA_DIR / 'backup'

LIVE_DATA_FILE = DATA_DIR / 'live_data.json'
MARKDOWN_FILE = DATA_DIR / 'live_data_tables.md'
CSV_FILE = DATA_DIR / 'live_data_trades.csv'

# =============================================================================
# EOD Handler Class
# =============================================================================

class EODBackupHandler:
    """Handles end-of-day backup and cleanup"""
    
    def __init__(self):
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.date_str = datetime.now().strftime('%Y-%m-%d')
        
        # Create directories if they don't exist
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    def backup_files(self) -> bool:
        """Backup all live data files"""
        try:
            print(f"\n📦 EOD BACKUP HANDLER - {self.date_str}")
            print("=" * 70)
            
            # Backup live_data.json
            if LIVE_DATA_FILE.exists():
                backup_file = ARCHIVE_DIR / f'live_data_{self.timestamp}.json'
                shutil.copy2(LIVE_DATA_FILE, backup_file)
                size = backup_file.stat().st_size
                print(f"✅ Backed up: live_data.json → {backup_file.name} ({size} bytes)")
            
            # Backup markdown tables
            if MARKDOWN_FILE.exists():
                backup_file = ARCHIVE_DIR / f'live_data_tables_{self.timestamp}.md'
                shutil.copy2(MARKDOWN_FILE, backup_file)
                size = backup_file.stat().st_size
                print(f"✅ Backed up: live_data_tables.md → {backup_file.name} ({size} bytes)")
            
            # Backup CSV
            if CSV_FILE.exists():
                backup_file = ARCHIVE_DIR / f'live_data_trades_{self.timestamp}.csv'
                shutil.copy2(CSV_FILE, backup_file)
                size = backup_file.stat().st_size
                print(f"✅ Backed up: live_data_trades.csv → {backup_file.name} ({size} bytes)")
            
            return True
        
        except Exception as e:
            print(f"❌ Error during backup: {e}")
            return False
    
    def clear_live_data(self) -> bool:
        """Clear live data files for next trading day"""
        try:
            print("\n🧹 CLEARING LIVE DATA FOR NEXT DAY")
            print("-" * 70)
            
            # Clear JSON file - keep structure but reset data
            if LIVE_DATA_FILE.exists():
                initial_data = build_empty_live_data(market_status="CLOSED")
                
                with open(LIVE_DATA_FILE, 'w') as f:
                    json.dump(initial_data, f, indent=2)
                print(f"✅ Cleared: live_data.json")
            
            # Clear markdown file
            if MARKDOWN_FILE.exists():
                MARKDOWN_FILE.write_text("# Live Data - No trades yet\n\n*Tables will be populated when trading starts*\n")
                print(f"✅ Cleared: live_data_tables.md")
            
            # Clear CSV file
            if CSV_FILE.exists():
                CSV_FILE.write_text(
                    "Trade ID     | Symbol                    | Action | Quantity | Status   | "
                    "Entry Premium   | Entry Value      | Alert Price  | Current Premium | "
                    "Current Value   | Highest Premium | Unrealized PNL  | Unrealized PNL %\n"
                    + "-" * 165 + "\n"
                )
                print(f"✅ Cleared: live_data_trades.csv")
            
            return True
        
        except Exception as e:
            print(f"❌ Error during clear: {e}")
            return False
    
    def show_archive_summary(self) -> None:
        """Show summary of archived files"""
        print("\n📁 ARCHIVE SUMMARY")
        print("-" * 70)
        
        archive_files = list(ARCHIVE_DIR.glob('*'))
        if archive_files:
            total_size = 0
            today_backups = 0
            
            for f in sorted(archive_files)[-10:]:  # Show last 10 files
                size = f.stat().st_size
                total_size += size
                
                if self.date_str in f.name:
                    today_backups += 1
                    print(f"  {f.name:<45} {size:>8} bytes")
            
            print("-" * 70)
            print(f"Today's backups: {today_backups} | Total archive size: {total_size:,} bytes")
        else:
            print("  No archived files yet")
    
    def cleanup_old_backups(self, days_to_keep: int = 30) -> None:
        """Remove backups older than specified days"""
        try:
            print(f"\n🗑️  CLEANUP OLD BACKUPS (keeping last {days_to_keep} days)")
            print("-" * 70)
            
            from datetime import timedelta
            cutoff_date = datetime.now() - timedelta(days=days_to_keep)
            
            deleted_count = 0
            for f in ARCHIVE_DIR.glob('*'):
                file_time = datetime.fromtimestamp(f.stat().st_mtime)
                if file_time < cutoff_date:
                    f.unlink()
                    deleted_count += 1
                    print(f"  🗑️  Deleted: {f.name}")
            
            if deleted_count == 0:
                print(f"  ℹ️  No files older than {days_to_keep} days")
            else:
                print(f"\n✅ Deleted {deleted_count} old backup file(s)")
        
        except Exception as e:
            print(f"❌ Error during cleanup: {e}")
    
    def run_eod(self, cleanup_days: int = 30) -> bool:
        """Run complete EOD backup and cleanup routine"""
        print("\n" + "=" * 70)
        print("🌅 END OF DAY (EOD) BACKUP HANDLER")
        print("=" * 70)
        
        # Step 1: Backup files
        if not self.backup_files():
            return False
        
        # Step 2: Clear live data
        if not self.clear_live_data():
            return False
        
        # Step 3: Show archive summary
        self.show_archive_summary()
        
        # Step 4: Cleanup old files
        self.cleanup_old_backups(cleanup_days)
        
        print("\n" + "=" * 70)
        print("✅ EOD BACKUP COMPLETE - Ready for next trading day!")
        print("=" * 70)
        
        return True

# =============================================================================
# Integration Helper
# =============================================================================

def run_eod_backup():
    """Simple function to call from bot code"""
    handler = EODBackupHandler()
    return handler.run_eod()

def get_eod_handler() -> EODBackupHandler:
    """Get EOD handler instance"""
    return EODBackupHandler()

# =============================================================================
# Main - For manual execution
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='EOD Backup Handler')
    parser.add_argument('--backup', action='store_true', help='Run full EOD backup')
    parser.add_argument('--clear', action='store_true', help='Clear live data only')
    parser.add_argument('--cleanup', type=int, default=30, help='Keep backups for N days (default: 30)')
    parser.add_argument('--summary', action='store_true', help='Show archive summary')
    
    args = parser.parse_args()
    
    handler = EODBackupHandler()
    
    if args.backup:
        handler.run_eod(cleanup_days=args.cleanup)
    elif args.clear:
        handler.clear_live_data()
    elif args.summary:
        handler.show_archive_summary()
    else:
        # Default: run full EOD
        handler.run_eod(cleanup_days=args.cleanup)

"""
USAGE EXAMPLES:

1. Manual execution - Run full EOD:
   python3 eod_backup_handler.py

2. Keep backups for 60 days:
   python3 eod_backup_handler.py --backup --cleanup 60

3. Just show summary:
   python3 eod_backup_handler.py --summary

4. Just clear live data:
   python3 eod_backup_handler.py --clear

5. From bot code at market close:
   from eod_backup_handler import run_eod_backup
   
   def on_market_close():
       run_eod_backup()  # Automatically backs up and clears

6. Setup cron job (runs daily at 3:35 PM):
   35 15 * * 1-5 cd /root/santhosh/trading && python3 eod_backup_handler.py
"""
