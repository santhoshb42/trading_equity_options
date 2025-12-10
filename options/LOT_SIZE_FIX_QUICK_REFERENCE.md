## Options Bot Lot Size Fix - Quick Reference

### What Changed?

**Problem:** Options bot was placing single contracts (qty=1) for all options
**Solution:** Now fetches correct lot size from instrument.json

### Two Code Changes

#### 1️⃣ Added `get_lot_size()` method
**File:** `options/optcode/instrument_manager.py` (lines 117-148)

```python
def get_lot_size(self, symbol: str) -> int:
    """Get lot size for symbol, returns 1 if not found"""
    contract = self.get_strike_by_symbol(symbol)
    if contract:
        try:
            lotsize = contract.get('lotsize')
            if lotsize:
                return int(lotsize)
        except (ValueError, TypeError):
            logger.warning(...)
    return 1  # Fallback
```

#### 2️⃣ Use lot size in order placement
**File:** `options/optcode/optapi.py` (lines 337-343)

```python
# Get lot size from instrument manager
lot_size = state['instrument_manager'].get_lot_size(selected_contract.symbol)
logger.debug(f"ALERT_PROCESS: LOT_SIZE | contract={selected_contract.symbol} | lotsize={lot_size}")

# Place order with correct quantity
order_id = state['broker'].place_options_order(
    symbol=selected_contract.symbol,
    action='BUY',
    quantity=lot_size,  ← CORRECT LOT SIZE!
    price=selected_contract.ltp,
    order_type='MARKET'
)
```

### Examples

| Symbol | Old Qty | New Qty | Difference |
|--------|---------|---------|-----------|
| JIOFIN30DEC25325CE | 1 | 2,350 | +2,349 ✅ |
| HDFCAMC30DEC252300PE | 1 | 300 | +299 ✅ |
| MOTHERSON27JAN26101CE | 1 | 6,150 | +6,149 ✅ |
| MCX30DEC2510500CE | 1 | 125 | +124 ✅ |

### Test It

```bash
cd /root/santhosh/trading/options
python3 test_lot_size.py
```

### Restart Bot

```bash
pkill -f "options/main.py"
sleep 2
python3 /root/santhosh/trading/options/main.py &
```

### Verify It Works

1. Send options alert via TradingView webhook
2. Check logs: `tail -f logs/[DATE]/detailed.log | grep "LOT_SIZE"`
3. See: `ALERT_PROCESS: LOT_SIZE | contract=JIOFIN30DEC25325CE | lotsize=2350` ✅

### Key Benefits

✅ Orders use correct lot size (no more rejections)  
✅ Quantity logged for transparency  
✅ Fallback to 1 if symbol not found  
✅ Works with all option symbols  

### Files Modified

- ✅ `options/optcode/instrument_manager.py` (+32 lines)
- ✅ `options/optcode/optapi.py` (-2 lines, +4 lines net)
- ✅ `options/test_lot_size.py` (NEW - test suite)
- ✅ `options/LOT_SIZE_FIX_IMPLEMENTATION.md` (NEW - full docs)

---

**Status:** ✅ IMPLEMENTED AND TESTED
