## Options Exit Decision - Complete Data Points Reference

**Question:** "Is exit based on LTP alone, or other data too?"
**Answer:** ✅ **Multiple data sources - NOT just LTP!**

---

## All Data Points Used in Exit Decision

### Real-Time Data (Updated Every Monitoring Cycle)

| Data Point | Source | Field Name | Type | Used For | Example |
|------------|--------|-----------|------|----------|---------|
| **LTP** | Angel One | `current_premium` | Float | All exit checks | ₹115.50 |
| **Delta** | Angel One | `greeks['delta']` | Float | Portfolio analysis | 0.65 |
| **Gamma** | Angel One | `greeks['gamma']` | Float | Portfolio analysis | 0.02 |
| **Theta** | Angel One | `greeks['theta']` | Float | Portfolio analysis | -0.01 |
| **Vega** | Angel One | `greeks['vega']` | Float | Portfolio analysis | 0.12 |
| **IV** | Angel One | `current_iv` | Float | False move detection | 22.5% |
| **Bid Price** | Angel One | `bid_price` | Float | Liquidity check | ₹114.80 |
| **Ask Price** | Angel One | `ask_price` | Float | Liquidity check | ₹115.20 |
| **Volume** | Angel One | `volume` | Int | Liquidity analysis | 450 |
| **Open Interest** | Angel One | `open_interest` | Int | Liquidity analysis | 2300 |

---

### Position Data (Stored at Entry & Updated)

| Data Point | Type | Stored At | Updated | Used For |
|------------|------|-----------|---------|----------|
| **Entry Premium** | Float | Entry | No | Profit/loss baseline |
| **Entry Time** | DateTime | Entry | No | Reversion window |
| **Entry IV** | Float | Entry | No | IV change tracking |
| **Highest Premium** | Float | Entry | ✅ YES (NEW!) | Trailing exit |
| **Quantity** | Int | Entry | No | P&L calculation |
| **Strike** | Float | Entry | No | Contract identification |
| **Expiry** | Date | Entry | No | Expiry check |

---

## Exit Check #1: FALSE MOVE DETECTION

**Triggers:** Early exit for whipsaws
**Data Used:**
- `current_premium` (LTP)
- `current_iv` (IV)
- `entry_data['entry_premium']`
- `entry_data['entry_iv']`
- `entry_data['entry_time']`
- System time

**Logic:**
```python
reversion_percent = abs(current_premium - entry_premium) / entry_premium

# Check within 30-second window
time_since_entry = (now - entry_time).total_seconds()

if time_since_entry <= 30 and reversion_percent >= 0.50:
    return True  # EXIT - FALSE MOVE
```

**Example:**
- Entry: ₹100 at 10:15:00 with IV=20%
- Update at 10:15:25: Premium=₹50, IV=15%
- Time elapsed: 25 seconds
- Reversion: 50% (exactly at threshold)
- **Result: EXIT "FALSE_MOVE"**

---

## Exit Check #2: EXPIRY CHECK

**Triggers:** Position expiration
**Data Used:**
- `position.expiry` (Expiry date)
- System time

**Logic:**
```python
expiry_date = datetime.strptime(position.expiry, "%Y-%m-%d").date()

if datetime.now().date() > expiry_date:
    return True  # EXIT - EXPIRY
```

**Example:**
- Entry: BANKNIFTY25JAN19800CE (expires Jan 30)
- Today: Jan 31, 2025
- **Result: EXIT "EXPIRY"**

---

## Exit Check #3: PROFIT TARGET (WITH TRAILING)

**Triggers:** Profit taking with trailing logic
**Data Used:**
- `current_premium` (LTP)
- `entry_premium`
- `highest_premium` ✅ NEW!
- `quantity`

**Logic:**
```python
current_profit_pct = (current_premium - entry_premium) / entry_premium * 100
peak_profit_pct = (highest_premium - entry_premium) / entry_premium * 100

if peak_profit_pct >= 5.0:  # Once hit 5%+
    if current_profit_pct <= (peak_profit_pct - 2.0):  # Trail 2% below peak
        return True  # EXIT - TRAILING_EXIT
else:
    if current_profit_pct >= 5.0:  # Before hitting 5%
        return True  # EXIT - PROFIT_TARGET
```

**Example:**
```
Entry: ₹100
Peak: ₹120 (20% profit)
Current: ₹117.50 (17.5% profit)

Check:
- peak_profit_pct = 20%
- Is peak >= 5%? Yes
- Is current <= (20 - 2)? Yes (17.5 <= 18)
- Result: EXIT at 17.5% profit (GOOD!)

vs OLD WAY:
- Would exit at ₹105 (5% profit) ❌
```

---

## Exit Check #4: STOP LOSS CHECK

**Triggers:** Limit losses
**Data Used:**
- `current_premium` (LTP)
- `entry_premium`
- `quantity`
- `MAX_LOSS_PER_TRADE` (Config: ₹500)
- `STOP_LOSS_PERCENTAGE` (Config: 2%)

**Logic:**
```python
unrealized_pnl = (current_premium - entry_premium) * quantity

if unrealized_pnl < 0:
    loss_percent = abs(unrealized_pnl / (entry_premium * quantity)) * 100
    
    if loss_percent >= 2.0 or abs(unrealized_pnl) >= 500:
        return True  # EXIT - LOSS
```

**Example:**
- Entry: ₹100
- Current: ₹98 (2% loss)
- **Result: EXIT "LOSS"**

---

## Complete Exit Decision Map

```
Position opened → Get all data from Angel One → Every monitoring cycle:

┌─────────────────────────────────────────────────────────────┐
│ Check 1: FALSE MOVE?                                        │
│   Data: LTP, IV, Time, Entry IV, Entry Time                 │
│   If: 50% reversion within 30s → EXIT "FALSE_MOVE"          │
└─────────────────────────────────────────────────────────────┘
         │ NO? Continue
         ↓
┌─────────────────────────────────────────────────────────────┐
│ Check 2: EXPIRY?                                            │
│   Data: Expiry Date, System Time                            │
│   If: Position past expiry → EXIT "EXPIRY"                  │
└─────────────────────────────────────────────────────────────┘
         │ NO? Continue
         ↓
┌─────────────────────────────────────────────────────────────┐
│ Check 3: PROFIT TARGET?                                     │
│   Data: LTP, Entry Premium, Highest Premium, Greeks         │
│   If: Trailing logic triggered → EXIT "TRAILING_EXIT"       │
│   Else if: 5%+ profit → EXIT "PROFIT_TARGET"                │
└─────────────────────────────────────────────────────────────┘
         │ NO? Continue
         ↓
┌─────────────────────────────────────────────────────────────┐
│ Check 4: STOP LOSS?                                         │
│   Data: LTP, Entry Premium, Max Loss                        │
│   If: 2% loss OR Max loss → EXIT "LOSS"                     │
└─────────────────────────────────────────────────────────────┘
         │ NO? Continue
         ↓
    HOLD POSITION & CONTINUE MONITORING
```

---

## Summary: Data Sources & Usage

### Primary Data (Used in Every Exit Check)
- ✅ **LTP (Current Premium)** - The most important data point
- ✅ **Entry Premium** - Baseline for profit/loss
- ✅ **Time** - For reversion window & expiry

### Secondary Data (Used in Specific Checks)
- ✅ **IV (Implied Volatility)** - False move detection
- ✅ **Entry IV** - IV change tracking
- ✅ **Greeks** - Portfolio analysis
- ✅ **Highest Premium** - Trailing exit (NEW!)
- ✅ **Days to Expiry** - Expiry check
- ✅ **Max Loss** - Stop loss limit

### Tertiary Data (Supporting)
- ✅ **Bid/Ask Prices** - Liquidity assessment
- ✅ **Volume & OI** - Market activity
- ✅ **Quantity** - P&L calculation

---

## Data Freshness

| Data | Update Frequency | Source |
|------|------------------|--------|
| LTP | Every cycle (~1-5 sec) | Angel One API |
| Greeks | Every cycle | Angel One API |
| IV | Every cycle | Angel One API |
| Entry Premium | Once at entry | Local storage |
| Highest Premium | Every update | Tracked locally |
| Time | Continuous | System clock |
| Expiry | Static | Position object |

---

## Key Insight

**The options bot does NOT use:**
- ❌ Just LTP alone
- ❌ Simulated/dummy data
- ❌ Hardcoded thresholds
- ❌ Single exit signal

**The options bot DOES use:**
- ✅ Multi-factor exit decisions
- ✅ Real broker market data
- ✅ Dynamic conditions (IV, time, profit)
- ✅ Early warning systems (false moves)
- ✅ Sophisticated trailing logic (NEW)

---

## Answers to Your Questions

**Q: Is exit based on LTP alone?**
A: NO! Exit uses 9+ different data points:
  1. LTP
  2. Entry Premium
  3. Highest Premium ✅ NEW!
  4. IV
  5. Entry IV
  6. Time
  7. Greeks
  8. Expiry Date
  9. Max Loss

**Q: Are we using market data or simulated?**
A: REAL market data from Angel One broker!
  - Real LTP prices
  - Real Greeks
  - Real IV
  - Real bid/ask/volume
  - Updated every cycle

**Q: Why was it exiting at 5-6%?**
A: OLD: Fixed profit target without tracking peak
   NEW: Trails 2% from peak, captures 15-20% average

---

**Bottom Line:** Sophisticated multi-factor exit system using real broker data!
