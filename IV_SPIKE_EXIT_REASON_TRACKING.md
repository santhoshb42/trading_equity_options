# IV_SPIKE Exit Reason Tracking

## Date: January 8, 2026
## Status: ✅ FULLY INTEGRATED INTO TRACKING SYSTEM

---

## Exit Reason Flow for IV_SPIKE

### 1. Position Close (optmonitor.py)
When IV spike is detected, position closes with reason:
```python
f"IV_SPIKE ({iv_rise_pct*100:.1f}% from entry)"
# Example: "IV_SPIKE (16.5% from entry)"
```

### 2. Exit Reason Saved Locations

#### a) `option_pnl_history.json` (Historical Record)
```json
{
  "symbol": "BANKNIFTY27JAN262200CE",
  "entry_premium": 45.20,
  "exit_premium": 52.65,
  "pnl": -2450.00,
  "exit_reason": "IV_SPIKE (16.5% from entry)",
  "closed_at": "2026-01-08T10:15:42.123456",
  ...
}
```

#### b) `live_data.json` (Current Closed Trades)
```json
{
  "symbol": "BANKNIFTY27JAN262200CE",
  "status": "CLOSED",
  "entry_premium": 45.20,
  "exit_premium": 52.65,
  "realized_pnl": -2450.00,
  "exit_reason": "IV_SPIKE (16.5% from entry)",
  ...
}
```

#### c) `live_data_trades.csv` (Display Format)
```
Sts | Underlying | Time  | Entry  | Exit   | High   | Qty    | PnL      | PnL%  | Dur   | Reason    | EntD   | EntG   | EntT   | ExD    | ExG    | ExT
----+------------+-------+--------+--------+--------+--------+----------+-------+-------+-----------+--------+--------+--------+--------+--------+--------
CLS | BANKNIFTY  | 10:15 |  45.20 |  52.65 |  48.00 |    100 |  -2450.0 |  -5.4 |   18m | IV_SPIKE  |  0.520 |  0.003 |  -1.68 |  0.510 |  0.005 |  -0.93
```

---

## Exit Reason Display

### In CSV (live_data_trades.csv)
The exit reason is shortened for display:
- **Full Reason**: `IV_SPIKE (16.5% from entry)`
- **Display**: `IV_SPIKE` (shortened to 8 characters max)

### Shortening Logic
```python
if 'IV_SPIKE' in exit_reason:
    exit_reason = 'IV_SPIKE'
elif 'IV_CRASH' in exit_reason:
    exit_reason = 'IV_CRASH'
elif 'MOMENTUM_REVERSAL' in exit_reason:
    exit_reason = 'MOMENTUM'
elif 'STOPLOSS' in exit_reason:
    exit_reason = 'STOPLOSS'
# ... other reasons
```

### All Exit Reasons (Shortened)
| Full Reason | Shortened | Meaning |
|---|---|---|
| `IV_SPIKE (X% from entry)` | `IV_SPIKE` | IV rose >15% from entry (panic signal) |
| `IV_CRASH (X% from entry)` | `IV_CRASH` | IV dropped >10% from entry (premium decay) |
| `MOMENTUM_REVERSAL (X% from peak)` | `MOMENTUM` | Price dropped 10% from peak |
| `TRIAL_SL_HIT` | `TRIAL_SL` | Trailing SL triggered |
| `STOPLOSS (X%)` | `STOPLOSS` | Hard stop loss at -20% |
| `TARGET_HIT` | `TARGET` | Profit target reached |
| `EXPIRY` | `EXPIRY` | Position expired at EOD |
| `EOD_SQUAREOFF` | `EOD_SQ` | End of day square off |

---

## Data Files Updated

### 1. `optmonitor.py` (Exit Reasons)
- ✅ IV_SPIKE close reason: `f"IV_SPIKE ({iv_rise_pct*100:.1f}% from entry)"`
- ✅ IV_CRASH close reason: `f"IV_CRASH ({iv_drop_pct*100:.1f}% from entry)"`
- ✅ Both saved to `_save_pnl_history()` with exit_reason field

### 2. `live_data_table_formatter.py` (CSV Display)
- ✅ Added IV_SPIKE shortening check
- ✅ Added IV_CRASH shortening check
- ✅ Positioned before other reason checks (priority: IV > MOMENTUM)

### 3. `live_data_tracker.py` (Already Tracking)
- ✅ `close_trade()` method accepts exit_reason parameter
- ✅ Records exit_reason in live_data.json

### 4. Files Automatically Generated
- ✅ `option_pnl_history.json`: Full exit reasons with details
- ✅ `live_data.json`: Summary with exit reasons for closed trades
- ✅ `live_data_trades.csv`: Shortened display format

---

## Example CSV Output (Next IV_SPIKE Exit)

When the next IV spike occurs and a position exits:

```
Last Updated: 2026-01-08 14:30:00

=== CLOSED TRADES (Today) ===
Sts | Underlying | Time  | Entry  | Exit   | High   | Qty    | PnL      | PnL%  | Dur   | Reason    | EntD   | EntG   | EntT   | ExD    | ExG    | ExT
----+------------+-------+--------+--------+--------+--------+----------+-------+-------+-----------+--------+--------+--------+--------+--------+--------
CLS | BANKNIFTY  | 14:25 |  45.20 |  52.65 |  48.00 |    100 |  -2450.0 |  -5.4 |   18m | IV_SPIKE  |  0.520 |  0.003 |  -1.68 |  0.510 |  0.005 |  -0.93
CLS | NIFTY      | 14:24 |  67.85 |  78.90 |  72.00 |     75 |  -8312.5 |  -8.2 |   25m | IV_SPIKE  |  0.480 |  0.004 |  -1.45 |  0.450 |  0.006 |  -0.88
CLS | FINNIFTY   | 14:20 |  23.40 |  28.50 |  25.00 |    400 |  -2040.0 |  -2.2 |   32m | MOMENTUM  |  0.510 |  0.002 |  -1.92 |  0.480 |  0.003 |  -1.05
```

---

## Full Exit Reason Tracking Chain

```
Position Entry
    ↓
Position.close_position()
    ↓
    Sets: position.exit_reason = "IV_SPIKE (16.5% from entry)"
    ↓
PositionMonitor.close_position()
    ↓
    Returns: {'exit_reason': 'IV_SPIKE (16.5% from entry)', ...}
    ↓
_save_pnl_history()
    ↓
    Saves to: option_pnl_history.json with full reason
    ↓
LiveDataTracker.close_trade()
    ↓
    Updates: live_data.json with exit_reason field
    ↓
LiveDataTableFormatter.generate_csv()
    ↓
    Shortens: 'IV_SPIKE (16.5% from entry)' → 'IV_SPIKE'
    ↓
    Writes to: live_data_trades.csv
```

---

## Verification Steps

### To verify IV_SPIKE exits are being tracked:

1. **Check full reason in JSON**:
   ```bash
   grep "IV_SPIKE" /root/santhosh/trading/options/data/option_pnl_history.json
   ```
   Output: `"exit_reason": "IV_SPIKE (16.5% from entry)"`

2. **Check live data**:
   ```bash
   cat /root/santhosh/trading/options/data/live_data.json | jq '.closed_trades[] | {symbol, exit_reason}'
   ```
   Output: `{"symbol": "BANKNIFTY27JAN262200CE", "exit_reason": "IV_SPIKE (16.5% from entry)"}`

3. **Check CSV display**:
   ```bash
   grep "IV_SPIKE" /root/santhosh/trading/options/data/live_data_trades.csv
   ```
   Output: `CLS | BANKNIFTY  | 14:25 |  45.20 |  52.65 | ... | IV_SPIKE  |`

---

## Testing on Next IV Spike

When next market crash with IV spike occurs:

1. **Expected Log Entry**:
   ```
   EARLY_EXIT_IV_SPIKE: BANKNIFTY | Entry IV: 45.20 | Current IV: 52.65 | 
   IV Rise: 16.5% (threshold: 15.0%) | PnL: ₹-2450.00 | Premium: ₹45.20 → ₹52.65
   ```

2. **Expected CSV Entry**:
   ```
   CLS | BANKNIFTY | HH:MM | 45.20 | 52.65 | ... | IV_SPIKE | ...
   ```

3. **Expected JSON Entry in option_pnl_history.json**:
   ```json
   {
     "symbol": "BANKNIFTY27JAN262200CE",
     "exit_reason": "IV_SPIKE (16.5% from entry)",
     "pnl": -2450.00
   }
   ```

---

## Commit Information

**Commit Hash**: c7ae3b7  
**Message**: Update exit reason tracking for IV_SPIKE and IV_CRASH  
**Date**: 2026-01-08 13:27 IST  
**Files Modified**: 5 files, 268 insertions  

---

## Summary

**IV_SPIKE exits are now fully integrated into the exit reason tracking system.**

When an IV_SPIKE exit occurs:
- ✅ Full reason saved: `IV_SPIKE (X.X% from entry)`
- ✅ Visible in `option_pnl_history.json` (historical record)
- ✅ Visible in `live_data.json` (current closed trades)
- ✅ Displayed in `live_data_trades.csv` (as 'IV_SPIKE')
- ✅ Logged with full details in application logs

This allows complete tracking and analysis of IV_SPIKE exits alongside all other exit mechanisms.

