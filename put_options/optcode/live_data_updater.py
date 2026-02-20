"""
Independent Live Data Updater Service

Updates live_data.json every 10 seconds from current position and PnL data.
Runs in a dedicated daemon thread to ensure JSON file is never stale.

Design rationale:
- Position monitor loop has multiple exit points that skip live_data.save()
- When LTP refresh times out or fails, live_data.json doesn't get updated
- This service ensures live_data.json is ALWAYS fresh, independent of LTP issues
- Updates every 10 seconds (matching original design requirement)

Output: /root/santhosh/trading/put_options/data/live_data.json
"""

import time
import threading
from pathlib import Path
from .live_data_tracker import get_live_data_tracker
from .optlogging import logger


def start_live_data_updater_service():
    """
    Start independent live data updater service
    Runs in daemon thread, updates every 10 seconds
    """
    def update_live_data_periodically():
        """Update live_data.json every 10 seconds"""
        logger.info("LIVE_DATA_UPDATER: SERVICE_STARTING | interval=10s")
        
        try:
            tracker = get_live_data_tracker()
        except Exception as e:
            logger.error(f"LIVE_DATA_UPDATER: INITIALIZATION_FAILED | {str(e)}")
            return
        
        logger.info("LIVE_DATA_UPDATER: SERVICE_STARTED")
        
        while True:
            try:
                time.sleep(10)  # Update every 10 seconds
                
                # Save live data from current positions and PnL
                success = tracker.save()
                
                if success:
                    live_data_file = Path('/root/santhosh/trading/put_options/data/live_data.json')
                    if live_data_file.exists():
                        file_size = live_data_file.stat().st_size
                        logger.debug(f"LIVE_DATA_UPDATER: Updated | file_size={file_size} bytes")
                    else:
                        logger.warning("LIVE_DATA_UPDATER: File not created after save()")
                else:
                    logger.debug("LIVE_DATA_UPDATER: Save returned False")
                    
            except Exception as e:
                logger.warning(f"LIVE_DATA_UPDATER: Error | {str(e)[:100]}")
                # Continue updating even if one cycle fails
                pass
    
    # Start in daemon thread
    updater_thread = threading.Thread(target=update_live_data_periodically, daemon=True)
    updater_thread.start()
    logger.info("LIVE_DATA_UPDATER: THREAD_STARTED")
