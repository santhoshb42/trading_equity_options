# Multi-Lot Trading: Daily Monitoring Checklist

**Deployment**: December 28, 2025  
**Review Frequency**: Daily (during market hours)

---

## 🟢 Green Status Indicators

### Order Entry
- [ ] Logs show `DYNAMIC_LOT_SIZING` message on each alert
- [ ] Message includes: `premium=₹XXXX, qty=YYY, actual_cost=₹ZZZZ, utilization=XX%`
- [ ] Quantity values: 150, 225, 300, 375, 450, 525, 600+ (not always 75)
- [ ] Actual cost shows ₹27,000-₹30,000 (not ₹4,500-₹6,000)
- [ ] Utilization shows 85%+ (not ~15%)

### Position Monitoring
- [ ] `/positions` API endpoint shows `quantity` > 75
- [ ] Example: `"quantity": 450` (not `"quantity": 75`)
- [ ] Entry premium total: `entry_premium × quantity` (e.g., 4500 × 450 = 2,025,000)
- [ ] Current premium total: scales with quantity
- [ ] Unrealized PnL shows 6x improvement over single-lot baseline

### Real-time P&L
- [ ] Dashboard updates PnL every 5 seconds
- [ ] PnL values are 6x higher than previous single-lot trades
  - If premium changes ₹50, multi-lot PnL should be ₹22,500 (not ₹3,750)
- [ ] PnL calculation: `(current_premium - entry_premium) × quantity`

### StopLoss Management
- [ ] SL orders placed with correct quantity (450, not 75)
- [ ] SL order logs show: `quantity=450, trigger_price=4300`
- [ ] When SL hits, all contracts sold (not just 75)
- [ ] SL modifications use same quantity

### Position Exit
- [ ] Close/exit orders show correct quantity
- [ ] EOD squareoff closes: `quantity=450, action=SELL`
- [ ] Manual exit logs show: `quantity=450, exit_premium=5175`

### P&L Recording
- [ ] Trade log CSV shows quantity column with values > 75
- [ ] Realized PnL scales with quantity: `(exit - entry) × quantity`
- [ ] JSON saved with correct quantity for backtesting

---

## 🔴 Red Flags - IMMEDIATE ACTION REQUIRED

| Flag | What's Wrong | Action |
|------|-------------|--------|
| `quantity=75` in all logs | Single-lot only | Check optapi.py line 1104 - verify import is `OptionsCapitalConfig` |
| `actual_cost=₹4,500` | Budget not being used | Verify `OptionsCapitalConfig.CAP_PER_TRADE = 30000` |
| `utilization=15%` | Old behavior | Review logs for error: "OptionsTradingConfig has no method" |
| PnL = `₹3,750` (not 22,500) | Using single lot | Check if `calculate_quantity_for_capital()` being called |
| Error: "OptionsTradingConfig.calculate_quantity_for_capital" | Method not found | This is the bug - class reference incorrect |
| `/positions` shows `"quantity": 75` | Single lot only | Restart optbot: `systemctl restart optbot.service` |

---

## Daily Tasks

### Before Market Open (09:00 AM)
```
☐ Bot running: systemctl status optbot.service (should show "active")
☐ Webhook listening: Check logs for "Webhook Server on 127.0.0.1:8081"
☐ Broker connected: Logs should show authentication successful
☐ Capital config loaded: Check CAP_PER_TRADE = 30000
☐ Lot sizes correct: BANKNIFTY=75, NIFTY=50, FINNIFTY=40
```

### During Market Hours (09:15 AM - 03:30 PM)
```
☐ First alert: Watch logs for DYNAMIC_LOT_SIZING message
☐ Entry quantity: Verify qty = (30000/premium) × lot_size
☐ Actual cost: Should be 27000-30000
☐ Utilization: Should be 85-100%
☐ Dashboard: Check /positions shows quantity > 75
☐ PnL scaling: Monitor if PnL is 6x baseline
☐ SL orders: Check quantity matches position
☐ Real-time monitoring: Every position shows correct quantity
```

### After Each Trade Exit
```
☐ Exit quantity: Matches entry quantity
☐ Realized PnL: Scales with quantity
☐ Trade log: CSV record includes quantity
☐ JSON save: Quantity persisted for recovery
```

### End of Day (After 3:30 PM)
```
☐ EOD squareoff: All exits use correct quantities
☐ Cleanup: Positions marked as closed
☐ Summary stats: Calculate daily capital deployed
☐ Daily report: Document avg_quantity, avg_utilization
```

---

## Weekly Tasks

### Every Monday
```
☐ Review last week's trades: Average quantity, utilization
☐ Compare to baseline: Week before fix (should be 6x improvement)
☐ Check P&L: Verify scaling (realized_pnl × 6)
☐ Spot check SL: Verify all SL orders use correct quantities
☐ Audit logs: Look for any "qty=75" entries (shouldn't exist)
```

### Every Friday
```
☐ Performance summary:
  - Total trades: N
  - Avg quantity: XXX (should be 300+)
  - Avg utilization: YY% (should be 85%+)
  - Total capital deployed: ₹ZZZ,000 (should be 6x baseline)
  - Total PnL: ₹AAA,000 (should be 6x baseline)
☐ Trend analysis: Is utilization holding steady?
☐ Risk check: Any anomalies in lot sizing?
```

---

## Monthly Tasks

### End of Month
```
☐ Performance Report:
  - Trades executed: N
  - Avg quantity: AAA
  - Avg utilization: XX%
  - Month total capital deployed: ₹B,BBB,000
  - Month PnL: ₹C,CCC,000
  
☐ Compare to Before (previous month):
  - Capital deployed: Before ₹N, After ₹6N (6x?)
  - Utilization: Before ~15%, After ~97% (6.5x?)
  - PnL: Before ₹X, After ₹6X (6x?)

☐ Verify savings: ~₹2.2M monthly waste eliminated
☐ Document: Update MULTI_LOT_PERFORMANCE.log
```

---

## Verification Commands

### Check Current Quantity Calculation

```bash
# SSH to server
ssh -i key.pem ubuntu@server_ip

# Check if multi-lot code is running
cd /root/santhosh/trading/options
grep -n "calculate_quantity_for_capital" optcode/optapi.py

# Should show: Line 1104+ calling OptionsCapitalConfig

# Test calculation manually
python3 << 'EOF'
from optcode.optconfig import OptionsCapitalConfig
# Test: 4500 premium, 30000 budget, 75 lot_size
qty = OptionsCapitalConfig.calculate_quantity_for_capital(4500, 30000, 75)
print(f"Quantity: {qty}")  # Should print: 450

# Test: 6000 premium
qty = OptionsCapitalConfig.calculate_quantity_for_capital(6000, 30000, 75)
print(f"Quantity: {qty}")  # Should print: 375
EOF
```

### Check Recent Logs

```bash
# View last 50 lines of logs
journalctl -u optbot.service -n 50 --no-pager

# Search for DYNAMIC_LOT_SIZING
journalctl -u optbot.service | grep "DYNAMIC_LOT_SIZING"

# Search for qty entries
journalctl -u optbot.service | grep -E "qty|quantity" | tail -20

# Check for errors
journalctl -u optbot.service | grep -i "error\|failed\|exception"
```

### Check Dashboard API

```bash
# Get current positions
curl http://localhost:8081/positions 2>/dev/null | python3 -m json.tool | grep -E "quantity|symbol|entry_premium"

# Should show:
# "quantity": 450 (or similar, not 75)
# "entry_premium": 4500
# "entry_premium_total": 2025000
```

### Verify Code Integrity

```bash
# Check optapi.py for correct class reference
grep -n "OptionsCapitalConfig.calculate_quantity_for_capital" optcode/optapi.py

# Should show exactly at line 1104:
# quantity = OptionsCapitalConfig.calculate_quantity_for_capital(

# Check for wrong reference (should find NONE)
grep -n "OptionsTradingConfig.calculate_quantity_for_capital" optcode/optapi.py
# Output: (empty - should be no matches)
```

---

## Example: What Good Logs Look Like

```
2025-12-28 10:30:15 | INFO | DYNAMIC_LOT_SIZING | premium=₹4500, budget=₹30000, base_lot_size=75
2025-12-28 10:30:15 | INFO | ORDER_PLACEMENT | quantity=450, actual_cost=₹27000, utilization=90%
2025-12-28 10:30:16 | INFO | ORDER_PLACED | symbol=BANKNIFTY27DEC4500CE, qty=450, price=4500
2025-12-28 10:30:20 | INFO | POSITION_CREATED | symbol=BANKNIFTY27DEC4500CE, quantity=450, entry_premium=4500
2025-12-28 10:30:25 | INFO | PNL_UPDATE | symbol=BANKNIFTY27DEC4500CE, current_premium=4550, unrealized_pnl=₹22500
2025-12-28 10:35:00 | INFO | SL_ORDER_PLACED | symbol=BANKNIFTY27DEC4500CE, qty=450, trigger=4300
2025-12-28 10:45:30 | INFO | TRAILING_SL | symbol=BANKNIFTY27DEC4500CE, new_sl=4600, qty=450
2025-12-28 11:00:00 | INFO | POSITION_CLOSED | symbol=BANKNIFTY27DEC4500CE, qty=450, exit_premium=5175
2025-12-28 11:00:05 | INFO | REALIZED_PNL | symbol=BANKNIFTY27DEC4500CE, pnl=₹303750, pnl_percent=15.0%
```

---

## Example: What Bad Logs Look Like (ALERT!)

```
2025-12-28 10:30:15 | INFO | ORDER_PLACEMENT | quantity=75, actual_cost=₹4500, utilization=15%
# ❌ Should be 450, ₹27000, 90%

2025-12-28 10:30:16 | INFO | ORDER_PLACED | symbol=BANKNIFTY27DEC4500CE, qty=75, price=4500
# ❌ Should be qty=450

2025-12-28 10:30:20 | ERROR | AttributeError: type object 'OptionsTradingConfig' has no attribute 'calculate_quantity_for_capital'
# ❌ Class reference is wrong - needs OptionsCapitalConfig

2025-12-28 10:30:25 | INFO | PNL_UPDATE | symbol=BANKNIFTY27DEC4500CE, unrealized_pnl=₹3750
# ❌ Should be ₹22500 (6x improvement)
```

---

## Emergency Procedures

### If Quantity Shows as 75 (Single Lot Only)

**Step 1: Verify Code**
```bash
grep -A5 "quantity = Options" optcode/optapi.py | head -10
```

**Expected**: Should show `OptionsCapitalConfig.calculate_quantity_for_capital(...)`  
**If Wrong**: Shows `OptionsTradingConfig.calculate_quantity_for_capital(...)`

**Step 2: Fix if Needed**
```bash
# Edit optapi.py line 1104
sed -i 's/OptionsTradingConfig\.calculate_quantity_for_capital/OptionsCapitalConfig.calculate_quantity_for_capital/g' optcode/optapi.py

# Restart bot
systemctl restart optbot.service

# Verify in logs
sleep 5
journalctl -u optbot.service -n 20 --no-pager | grep -i "dynamic\|quantity"
```

**Step 3: Test Calculation**
```bash
python3 << 'EOF'
from optcode.optconfig import OptionsCapitalConfig
qty = OptionsCapitalConfig.calculate_quantity_for_capital(4500, 30000, 75)
print(f"Result: {qty}")  # Should be 450
EOF
```

### If Actual Cost Shows ₹4,500 (Not Using Budget)

**Check CAP_PER_TRADE Setting**:
```bash
grep "CAP_PER_TRADE" optcode/optconfig.py
```

**Should show**: `CAP_PER_TRADE = 30000`  
**If different**: Update to 30000 and restart bot

### If Utilization Shows 15% (Old Behavior)

**Entire calculation is wrong**. This means `calculate_quantity_for_capital()` is NOT being called.

```bash
# Check if function is being called at all
grep -n "calculate_quantity_for_capital" optcode/optapi.py

# If line 1104 doesn't have it: Add it back
# Edit optapi.py around line 1104 and ensure:
# quantity = OptionsCapitalConfig.calculate_quantity_for_capital(premium=..., capital=..., lot_size=...)

# Restart
systemctl restart optbot.service
```

---

## Contact & Escalation

If any red flags detected:

1. **Check logs first**: `journalctl -u optbot.service`
2. **Verify code**: `grep -n "calculate_quantity_for_capital" optcode/optapi.py`
3. **Test calculation**: Run Python test above
4. **Restart if needed**: `systemctl restart optbot.service`
5. **Document issue**: Save logs to `logs/multi_lot_issue_YYYY-MM-DD.txt`

---

**Last Updated**: December 28, 2025  
**Next Review**: Tomorrow (Dec 29) during market hours

