"""
Periodic CSV Update Service

Ensures live_data_trades.csv is always up-to-date with current positions.
Runs independently every 30 seconds to refresh the file.
"""

import threading
import time
from pathlib import Path


_last_csv_payload = None

def start_csv_update_service():
    """Start background thread that updates CSV every 30 seconds"""
    
    def update_csv_periodically():
        """Periodically update CSV file with current position data"""
        while True:
            try:
                time.sleep(30)  # Update every 30 seconds
                
                # Import here to avoid circular imports
                from .live_data_table_formatter import get_table_formatter
                
                formatter = get_table_formatter()
                csv_data = formatter.generate_csv()
                global _last_csv_payload
                if csv_data == _last_csv_payload:
                    continue
                
                csv_file = Path('/root/santhosh/trading/options/data/live_data_trades.csv')
                with open(csv_file, 'w') as f:
                    f.write(csv_data)
                _last_csv_payload = csv_data
                
                # Log timestamp
                from .optlogging import logger
                logger.debug(f"CSV_UPDATER: Updated | file size={len(csv_data)} bytes")
                
            except Exception as e:
                from .optlogging import logger
                logger.warning(f"CSV_UPDATER: Error | {str(e)[:100]}")
                pass  # Continue running even if update fails
    
    # Start as daemon thread
    thread = threading.Thread(
        target=update_csv_periodically,
        daemon=True,
        name="CSVUpdater"
    )
    thread.start()
    return thread
