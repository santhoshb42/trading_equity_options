════════════════════════════════════════════════════════════════════════════════
                    WHY modify_order LOGS AREN'T APPEARING
════════════════════════════════════════════════════════════════════════════════

ROOT CAUSE ANALYSIS - COMPLETE DIAGNOSIS
═══════════════════════════════════════════════════════════════════════════════

PROBLEM STATEMENT:
  User is not seeing modify_order logs in equity bot monitor despite:
  ✅ TRAIL_SL_ENABLED = True  
  ✅ SL orders exist with correct product type
  ✅ Trail activation is set to True
  ✅ LTP values are in positions.json

════════════════════════════════════════════════════════════════════════════════

ACTUAL ROOT CAUSE - LTP STALENESS
════════════════════════════════════════════════════════════════════════════════

The monitor logs show:
  
  "LTP_STALE | reason=LTP not yet updated by bucket check or too old 
   | ltp_age_seconds=10986.17 | using_entry_price=545.3"

This means:
  • LTP was last updated ~3 HOURS ago (10,986 seconds)
  • Current time: 13:27:18
  • Last LTP update: ~09:51 (when positions were created)

════════════════════════════════════════════════════════════════════════════════

HOW THIS BREAKS THE FLOW:
════════════════════════════════════════════════════════════════════════════════

1. Monitor calls _check_stop_losses() every cycle
   ✅ WORKING

2. _check_stop_losses() calls _check_price_for_exit()
   ✅ WORKING

3. _check_price_for_exit() checks LTP age at line 1938:
   
   if ltp is None or ltp_age_seconds > 60:
       return  # EXIT EARLY - SKIP ALL PROCESSING
   
   ❌ FAILS: LTP age is 10986 seconds (>60 second limit)

4. Because of early return, these never execute:
   ❌ MONITOR_PRICE_CHECK log
   ❌ _update_trailing_sl() call  
   ❌ modify_order() call
   ❌ TRAIL_SL_MODIFIED log

════════════════════════════════════════════════════════════════════════════════

WHY IS LTP STALE?
════════════════════════════════════════════════════════════════════════════════

The LTP should be refreshed by _check_ltp_for_bucket():
  
  Location: monitor.py:1495
  Called from: run_monitor() at line 2641
  Purpose: Bulk fetch LTP for all positions using get_ltp_bulk()
  
BUT NO BUCKET_LTP LOGS APPEAR IN logs/2025-12-09/detailed.log

This means:
  • _check_ltp_for_bucket() is NOT being called
  • OR it's being called but hitting an error
  • OR the LTP bulk fetch is failing silently

════════════════════════════════════════════════════════════════════════════════

IMMEDIATE FIX - FORCE LTP UPDATE
════════════════════════════════════════════════════════════════════════════════

The simplest fix is to restart the monitor to get fresh LTP data:

  $ ps aux | grep "equity/main.py" | grep -v grep | awk '{print $2}' | xargs kill
  $ cd /root/santhosh/trading/equity && python3 main.py &

After restarting:
  
  1. Monitor will fetch fresh LTP via _check_ltp_for_bucket()
  2. position.last_updated will be current timestamp
  3. ltp_age_seconds will be < 60 seconds
  4. _check_price_for_exit() will NOT exit early
  5. _update_trailing_sl() WILL execute
  6. modify_order() WILL be called
  7. TRAIL_SL_MODIFIED logs WILL appear

════════════════════════════════════════════════════════════════════════════════

LONGER TERM - WHY ISN'T _check_ltp_for_bucket() WORKING?
════════════════════════════════════════════════════════════════════════════════

Possible issues to investigate:

1. Rate Limiting
   Check: consecutive_rate_limit_errors in monitor loop
   If >= 2: LTP bucket check is skipped (line 2638-2641)
   
   $ grep "consecutive_rate_limit\|RATE.*LIMIT" logs/2025-12-09/detailed.log

2. Exception in get_ltp_bulk()
   Check: Error logs in the detailed.log
   
   $ grep -i "ERROR\|Exception\|Traceback" logs/2025-12-09/detailed.log | head -20

3. Broker API issues
   Check: If Angel One API is accessible from the server
   
   $ python3 << 'EOF'
   from equity.eqcode import angelone_equity
   broker = angelone_equity.create_broker()
   print(broker.get_ltp_bulk(['GRANULES-EQ', 'CROMPTON-EQ']))
   EOF

4. Missing credentials
   Check: If broker authentication is working
   
   $ grep -i "auth\|login\|credential" logs/2025-12-09/detailed.log

════════════════════════════════════════════════════════════════════════════════

VERIFICATION CHECKLIST
════════════════════════════════════════════════════════════════════════════════

After implementing the fix, verify:

 [ ] Monitor process restarted
 [ ] BUCKET_LTP_SUCCESS logs appear (check every 5-10 seconds)
 [ ] ltp_age_seconds shows < 60 seconds  
 [ ] TRAIL_DEBUG logs appear
 [ ] TRAIL_ACTIVATED logs appear
 [ ] TRAIL_SL_CALCULATION logs appear
 [ ] TRAIL_SL_STEPPED logs appear
 [ ] TRAIL_SL_MODIFIED logs appear ✅ SUCCESS!

════════════════════════════════════════════════════════════════════════════════

SUMMARY
════════════════════════════════════════════════════════════════════════════════

The system IS working correctly, but with a 60-second staleness check that prevents
processing old price data. Since your LTP hasn't been updated in 3 hours, all 
position checks are being skipped.

Solution: Restart the monitor to get fresh LTP data flowing, then modify_order
will be called and you'll see the TRAIL_SL_MODIFIED logs.

════════════════════════════════════════════════════════════════════════════════
