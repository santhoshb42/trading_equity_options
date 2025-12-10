# NO_OF_LOTS Configuration - Options Bot Scaling

## Overview

The `NO_OF_LOTS` variable allows you to easily scale trade sizes for the options bot without code changes. This is perfect for gradually increasing your options trading size as you build confidence and capital.

---

## How It Works

**Formula:** 
```
Order Quantity = Base Lot Size × NO_OF_LOTS
```

**Example:**
- Lot size for JIOFIN30DEC25325CE = 2,350
- NO_OF_LOTS = 1
- **Order Quantity = 2,350 × 1 = 2,350 shares (1 contract)**

- NO_OF_LOTS = 2
- **Order Quantity = 2,350 × 2 = 4,700 shares (2 contracts)**

- NO_OF_LOTS = 3
- **Order Quantity = 2,350 × 3 = 7,050 shares (3 contracts)**

---

## Configuration Files

### File 1: `.env` (Actual Configuration)
```dotenv
# Number of lots per trade (for scaling: increase this to scale trade size)
# Each option contract = 1 lot
# Example: NO_OF_LOTS=1 places 1 lot (qty=lot_size), NO_OF_LOTS=2 places 2 lots (qty=2*lot_size)
NO_OF_LOTS=1
```

**Location:** `/root/santhosh/trading/options/.env`

### File 2: `.env.template` (Template for New Deployments)
Same variable with documentation.

**Location:** `/root/santhosh/trading/options/.env.template`

### File 3: `optconfig.py` (Python Configuration)
```python
class OptionsTradingConfig:
    # Number of lots per trade (for scaling trade size)
    # Each option contract = 1 lot (qty = lot_size * NO_OF_LOTS)
    # When scaling: increase this to increase trade size proportionally
    # Example: NO_OF_LOTS=1 → qty=lot_size, NO_OF_LOTS=2 → qty=2*lot_size
    NO_OF_LOTS = int(os.getenv("NO_OF_LOTS", "1"))  # Default 1 lot per trade
```

**Location:** `/root/santhosh/trading/options/optcode/optconfig.py` (Line 180)

---

## How Quantity is Calculated

The order placement logic in `optapi.py` (Lines 337-346):

```python
# Get lot size from instrument manager and apply NO_OF_LOTS multiplier for scaling
from optcode.optconfig import OptionsTradingConfig
base_lot_size = state['instrument_manager'].get_lot_size(selected_contract.symbol)
no_of_lots = OptionsTradingConfig.NO_OF_LOTS
quantity = base_lot_size * no_of_lots

logger.debug(f"ALERT_PROCESS: LOT_SIZE | contract={selected_contract.symbol} | base_lotsize={base_lot_size} | no_of_lots={no_of_lots} | qty={quantity}")

logger.info(f"ALERT_PROCESS: PLACING_ORDER | contract={selected_contract.symbol} | qty={quantity} | premium=₹{selected_contract.ltp:.2f}")
```

---

## Usage Examples

### Conservative Start: NO_OF_LOTS = 1
```
Alert: JIOFIN-BUY
Base Lot Size: 2,350
NO_OF_LOTS: 1
Order Quantity: 2,350 shares (1 contract)
Capital Risk: ~₹294,000 @ ₹125/share premium
```

### Moderate Scaling: NO_OF_LOTS = 2
```
Alert: JIOFIN-BUY
Base Lot Size: 2,350
NO_OF_LOTS: 2
Order Quantity: 4,700 shares (2 contracts)
Capital Risk: ~₹588,000 @ ₹125/share premium
```

### Aggressive Scaling: NO_OF_LOTS = 3
```
Alert: JIOFIN-BUY
Base Lot Size: 2,350
NO_OF_LOTS: 3
Order Quantity: 7,050 shares (3 contracts)
Capital Risk: ~₹882,000 @ ₹125/share premium
```

### Different Option Symbols

| Symbol | Base Lot Size | NO_OF_LOTS=1 | NO_OF_LOTS=2 | NO_OF_LOTS=3 |
|--------|---------------|--------------|--------------|--------------|
| JIOFIN30DEC25325CE | 2,350 | 2,350 | 4,700 | 7,050 |
| HDFCAMC30DEC252300PE | 300 | 300 | 600 | 900 |
| MOTHERSON27JAN26101CE | 6,150 | 6,150 | 12,300 | 18,450 |
| MCX30DEC2510500CE | 125 | 125 | 250 | 375 |

---

## Scaling Strategy

### Phase 1: Testing (NO_OF_LOTS = 1)
- Trade 1 lot per alert
- Validate strategy with small positions
- Monitor win rate and Greeks accuracy
- Capital requirement: ~₹30,000 per trade

### Phase 2: Scaling (NO_OF_LOTS = 2)
- Double your position size
- Monitor capital utilization
- Check margin requirements
- Capital requirement: ~₹60,000 per trade

### Phase 3: Expansion (NO_OF_LOTS = 3+)
- Further increase positions
- Maintain risk management rules
- Ensure sufficient capital buffer
- Capital requirement: ₹90,000+ per trade

---

## How to Scale

### Step 1: Update .env
```bash
# Edit the .env file
vi /root/santhosh/trading/options/.env
```

Change:
```dotenv
NO_OF_LOTS=1
```

To:
```dotenv
NO_OF_LOTS=2
```

### Step 2: Restart Bot
```bash
pkill -f "options/main.py"
sleep 2
cd /root/santhosh/trading/options && python3 main.py &
```

### Step 3: Send Test Alert
Send a test alert via TradingView to verify the new order size is being used.

### Step 4: Monitor Logs
```bash
tail -f logs/[DATE]/detailed.log | grep "LOT_SIZE"
```

Expected output for NO_OF_LOTS=2:
```
DEBUG | ALERT_PROCESS: LOT_SIZE | contract=JIOFIN30DEC25325CE | base_lotsize=2350 | no_of_lots=2 | qty=4700
INFO | ALERT_PROCESS: PLACING_ORDER | contract=JIOFIN30DEC25325CE | qty=4700 | premium=₹125.50
```

---

## Capital Impact

| NO_OF_LOTS | Per-Trade Capital | Max Slots | Total Capital |
|------------|-------------------|-----------|----------------|
| 1 | ~₹30,000 | 30 | ~₹900,000 |
| 2 | ~₹60,000 | 30 | ~₹1,800,000 |
| 3 | ~₹90,000 | 30 | ~₹2,700,000 |
| 4 | ~₹120,000 | 30 | ~₹3,600,000 |

**Note:** Adjust based on actual option premiums and your available capital.

---

## Important Notes

✅ **Easy Scaling:** Just change one variable to scale entire operation

✅ **No Code Changes:** No need to modify order placement logic

✅ **Automatic:** Capital calculations adjust automatically

⚠️ **Monitor Capital:** Ensure you have sufficient capital before increasing NO_OF_LOTS

⚠️ **Margin Requirements:** Check broker margin requirements for multiple contracts

⚠️ **Risk Management:** Maintain proper stop loss and position management

---

## Verification Checklist

- [ ] NO_OF_LOTS added to .env
- [ ] NO_OF_LOTS added to optconfig.py
- [ ] Bot restarted
- [ ] Test alert sent
- [ ] Logs show correct quantity calculation
- [ ] Order placed successfully

---

## Example Log Output

When NO_OF_LOTS=2 and you send an alert for JIOFIN-BUY:

```
[2025-12-09 15:42:30] INFO | ALERT_PROCESS: WEBHOOK_RECEIVED | symbol=JIOFIN | action=BUY
[2025-12-09 15:42:31] INFO | ALERT_PROCESS: OPTION_CHAIN_FETCHED | total=45 | atm=JIOFIN30DEC25325CE
[2025-12-09 15:42:31] DEBUG | ALERT_PROCESS: GREEKS_OK | delta=0.650 | gamma=0.00234
[2025-12-09 15:42:32] DEBUG | ALERT_PROCESS: LOT_SIZE | contract=JIOFIN30DEC25325CE | base_lotsize=2350 | no_of_lots=2 | qty=4700
[2025-12-09 15:42:32] INFO | ALERT_PROCESS: PLACING_ORDER | contract=JIOFIN30DEC25325CE | qty=4700 | premium=₹125.50
[2025-12-09 15:42:33] INFO | ALERT_PROCESS: ORDER_PLACED | order_id=12345678 | symbol=JIOFIN30DEC25325CE | qty=4700
[2025-12-09 15:42:34] INFO | MONITOR: POSITION_ADDED | contract=JIOFIN30DEC25325CE | qty=4700 | entry=125.50
```

Key lines to look for:
- `no_of_lots=2` ← Your scaling multiplier
- `qty=4700` ← Final calculated quantity (2350 × 2)

---

## Troubleshooting

**Issue: Logs show NO_OF_LOTS=1 when I changed it to 2**
- Solution: Restart bot to reload .env file
- Command: `pkill -f "options/main.py" && sleep 2 && python3 main.py &`

**Issue: OLD_QUANTITY still being used (e.g., qty=2350 when NO_OF_LOTS=2)**
- Solution: Check .env was saved, restart bot
- Verify: `grep NO_OF_LOTS /root/santhosh/trading/options/.env`

**Issue: Capital exceeded when increasing NO_OF_LOTS**
- Solution: Reduce MAX_SLOTS or reduce capital per trade
- Alternative: Close some existing positions before scaling

---

## Summary

✨ **NO_OF_LOTS provides a simple, effective way to scale your options trading without code changes.**

Use this variable to gradually increase your trade size as you:
- Build confidence in your strategy
- Accumulate more capital
- Improve your win rate
- Expand your trading operations

Simply change one line in `.env` and restart the bot!
