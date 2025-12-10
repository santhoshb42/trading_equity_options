# Hybrid Strategy - Monitor Quick Reference

**Effective Date**: December 11, 2025  
**Strategy**: Hybrid (PDC + Quality Filters + 1 Alert/Day)

## Alert Characteristics You'll See

### Frequency
- **Maximum**: 1 alert per symbol per trading day
- **Window**: 9:30 AM - 10:30 AM IST (but typically fires 9:30-9:45)
- **When**: When all quality criteria are met

### Alert Quality Indicators

Every alert will include these fields in JSON:
```json
{
  "symbol": "SBIN",
  "price": "625.50",
  "pdc_confirm": "1",              // Always 1 if alert fired (price > PDC)
  "pdc": "624.20",                 // Previous day close
  "adx": "13.5",                   // Directional strength (threshold 12-15)
  "atr_pct": "0.095",              // Volatility in % (threshold 0.08-0.12%)
  "rsi": "56.2",                   // Momentum (threshold 55-57)
  "ema9": "625.10",                // Short-term trend
  "ema20": "623.50",               // Long-term trend
  "vwap": "625.30",                // Fair value
  "score": "100.0"                 // Quality score (max 100)
}
```

## Quality Checklist

For each alert you receive, verify:
- ✅ `pdc_confirm = 1` → Price is above previous day close
- ✅ `adx >= 12` → Directional strength present
- ✅ `atr_pct >= 0.08%` → Meaningful volatility
- ✅ `rsi >= 55` → Not oversold
- ✅ `ema9 > ema20` → Uptrend in progress
- ✅ `close > vwap` → Price above fair value
- ✅ `score >= 80` → Overall quality above 80%

**If all 7 checks pass**: High-quality entry signal → Proceed with TRIAL

## Expected Signal Frequency

### By Session Time

| Time Window | Alert Probability | Filter Level | Strategy |
|-------------|-------------------|--------------|----------|
| 9:30-9:35 AM | High (if quality met) | ADX ≥12, ATR ≥0.08% | Early momentum capture |
| 9:35-9:40 AM | Medium | ADX ≥13, ATR ≥0.10% | Wait for confirmation |
| 9:40-9:45 AM | Medium | ADX ≥15, ATR ≥0.12% | Stronger momentum needed |
| 9:45+ AM | Low | ADX ≥20, ATR ≥0.15% | Full strict mode |

### Example Alert Patterns

**Good Pattern** (High Quality):
```
09:30:42 - SBIN alert: pdc_confirm=1, adx=13.2, atr=0.095%, score=95 ✅
(No more SBIN alerts today even if price keeps rising)
```

**Also Good** (Delayed Entry):
```
09:33:15 - ZYDUSLIFE alert: pdc_confirm=1, adx=14.1, atr=0.088%, score=88 ✅
(Different symbol, received after time, still high quality)
```

**Watch Out** (Rare with new filters):
```
If you get pdc_confirm=0 → Price not above PDC → SKIP this alert ❌
(Shouldn't happen with hybrid filters, but worth checking)
```

## Monitor Workflow Per Alert

```
Alert Received
    ↓
[Check pdc_confirm = 1?]
    ├─ YES → Continue
    └─ NO  → Skip (price not above PDC)
    ↓
[Check quality score >= 80?]
    ├─ YES → Continue
    └─ NO  → Consider skipping
    ↓
[Check ADX >= 12, ATR >= 0.08%, RSI >= 55?]
    ├─ YES → Continue
    └─ NO  → Skip (filters not met)
    ↓
TRIAL ENTRY
    ├─ Market order (immediate)
    └─ Limit order (better price, risk missing)
    ↓
MANUAL SL PLACEMENT
    ├─ Previous bar low -1 tick
    ├─ PDC - 1 tick
    ├─ Or based on your analysis
    └─ Set hard stop in broker
    ↓
MONITOR POSITION
    ├─ Scale out on 1R+ profit
    ├─ Trail SL for runners
    └─ Close on momentum loss
```

## Key Differences from Previous Version

| Aspect | Old (Ultra-Relaxed) | New (Hybrid) | Impact |
|--------|---------------------|-------------|--------|
| ADX threshold | 6 | 12 | Filters 2x more whipsaws |
| ATR threshold | 0.02% | 0.08% | Requires 4x more volatility |
| RSI threshold | 50 | 55 | Avoids oversold bounces |
| PDC check | None | Required | Filters gap-down false breaks |
| Alert frequency | Multiple/day | Max 1/day | Less over-trading |
| Expected P&L | -₹609 (9/10 losing) | +? (under testing) | Fewer signals, higher quality |

## Common Scenarios

### Scenario 1: No Alert Until 9:45 AM
**Why**: Quality criteria might not be met in 9:30-9:45 window
- Low ADX (weak direction)
- Low ATR (minimal volatility)
- RSI < 55 (oversold)
- Price below PDC (gap-down day)

**Action**: Wait for 9:45+ alert with stricter filters (ADX ≥20, ATR ≥0.15%)

### Scenario 2: Alert at 9:30:42, No More Alerts Today
**Why**: 1-alert-per-day limit reached for that symbol
- Even if price continues rallying
- Even if ADX increases
- Flag stays true until next day

**Action**: This is working as designed. Only 1 chance per symbol per day.

### Scenario 3: Received 5 Different Symbol Alerts in 9:30-9:45
**Why**: Quality conditions met for multiple stocks simultaneously
- Opening rush momentum across sector
- Each symbol gets max 1 alert

**Action**: Prioritize by score/quality. Monitor can only trial 2-3 positions anyway.

### Scenario 4: Alert with High Confidence (95) but Low Score (60)
**Why**: Confidence and score are different metrics
- Confidence = 95% alert was triggered correctly
- Score = Only 60% quality (missing some filters)

**Action**: Verify all quality checklist items before trading.

## Typical Daily Pattern

```
9:30 AM - Market opens
  └─ Alerts for 3-4 stocks if quality met

9:30-9:35 AM - Opening rush window
  └─ Most alerts fire here (early quality entries)
  └─ Your monitor should TRIAL these

9:35-9:45 AM - Confirmation window
  └─ Few additional alerts (stricter filters now)
  └─ Usually better setups for larger moves

9:45-10:30 AM - Full strict mode
  └─ Very few alerts (ADX ≥20 required)
  └─ These are strong reversals/breakouts

10:30 AM onwards - Session ends
  └─ No more alerts (time window closed)
```

## Alert Optimization Tips

1. **Set Webhook Alerts**: Configure TradingView to send alerts to your webhook
   - JSON will be formatted with all quality metrics
   - Use phone notification or Telegram

2. **Auto-Route High-Score Alerts**: 
   - If score >= 90: Priority entry
   - If score 80-89: Verify first
   - If score < 80: Consider skipping

3. **Track by Symbol**:
   - Keep list of symbols that alerted today
   - Ignore future signals from same symbol
   - Prevents accidental re-entry

4. **Compare with Market Strength**:
   - ADX > 15: Follow the trend
   - ADX 12-15: Be cautious with scaling
   - RSI > 60: Good for continuation trades

## Expected Improvements

Based on filter changes (Dec 10 data: 9/10 losing, -₹609):

**Optimistic Estimate** (if hybrid works):
- Win rate: 50% → 70%+ (fewer whipsaws)
- Avg P&L: -₹60/trade → +₹50-100/trade
- Daily P&L: -₹600 → +₹300-500 (if 5 alerts)

**Conservative Estimate**:
- Win rate: 50% → 60% (some improvement)
- Avg P&L: -₹60/trade → +₹20/trade
- Daily P&L: -₹600 → +₹50-100 (if 5 alerts)

**Baseline for Comparison**:
- Dec 10 ultra-relaxed: 10 trades, 9 losing, -₹609 total
- New hybrid should show improvement in quality

## Troubleshooting

### Problem: No alerts all day
**Check**:
1. Is market open 9:30-10:30 IST?
2. Are stocks trading above PDC?
3. Is ADX >= 12 in any stock?
4. Is ATR >= 0.08%?

### Problem: Alerts look weak (RSI < 50, ADX < 10)
**Explanation**: This shouldn't happen with hybrid filters. Check alert source:
- Might be from old webhook still firing
- Verify you're connected to updated Pine Script

### Problem: Received 10 alerts but said max 1 per symbol
**Check**:
- Are they different symbols? Each gets max 1, so 10 symbols = 10 alerts
- Or is it old alerts from previous day? Check timestamp

### Problem: Alert shows pdc_confirm=0
**This is an error**: Should never happen if alert fired
- Might indicate Pine Script bug
- Report with symbol, time, and data
- Skip trading until verified

## Discord/Telegram Alert Format

You should receive alerts like:
```
🚀 SBIN @ 625.50
Score: 95 | PDC: ✅ | ADX: 13.2 | ATR: 0.095% | RSI: 56.2
EMA9: 625.10 | VWAP: 625.30 | Time: 09:30:42
→ TRIAL NOW
```

If format is different, check webhook configuration.

---

**Last Updated**: December 10, 2025  
**Status**: Ready for deployment Dec 11, 2025  
**Test Window**: 9:30-10:30 AM IST
