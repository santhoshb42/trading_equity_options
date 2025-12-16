# US Market Migration - Quick Reference

## Effort Summary

```
┌─────────────────────────────────────────┐
│  TOTAL EFFORT: 4-6 Weeks (180-200 hrs)  │
└─────────────────────────────────────────┘

Week 1-2: Broker Abstraction Layer     [60 hrs] ████████
Week 2-3: Signal & Config Adaptation   [40 hrs] █████
Week 3-4: Monitoring & Exit Logic      [35 hrs] ████
Week 4-5: Testing & Validation         [50 hrs] ██████
Week 5-6: Buffer & Go-Live             [25 hrs] ███
```

## Complexity Breakdown

| Component | Effort | Risk | Reusability |
|-----------|--------|------|------------|
| **Broker Integration** | ★★★★★ (40h) | HIGH | 0% |
| **Symbol Mapping** | ★★★☆☆ (20h) | HIGH | 20% |
| **Market Hours** | ★★☆☆☆ (8h) | MED | 30% |
| **Position Monitoring** | ★★☆☆☆ (12h) | LOW | 85% ✅ |
| **Exit Logic** | ★☆☆☆☆ (5h) | LOW | 95% ✅ |
| **Greeks Integration** | ★★★☆☆ (15h) | MED | 40% |
| **Testing** | ★★★★☆ (40h) | MED | 0% |

## Architecture Changes

### Current (AngelOne - India)
```
TradingView → Port 80 Router → Equity Bot (8080) + Options Bot (8081)
                                   ↓              ↓
                            AngelOne API      AngelOne API
                            NSE/-EQ format    NFO/Options
```

### Target (Interactive Brokers - USA)
```
TradingView → Port 80 Router → Equity Bot (8080) + Options Bot (8081)
                                   ↓              ↓
                            IB API (Socket)   IB API (Socket)
                            Stocks/ETF        SPX/Options
```

## Key Integrations Needed

### 1. Broker Interface (NEW)
```python
# Abstract both AngelOne and IB under common interface
broker.place_order(symbol, action, qty, price)
broker.get_order_status(order_id)
broker.get_greeks(option_symbol)
broker.ensure_connection()
```

### 2. Contract Resolution (NEW)
```
Symbol: "AAPL"                    Symbol: "SPX"
   ↓                                 ↓
Contract Object              Contract Object
   ↓                                 ↓
IB conId: 265598             IB conId: 756629
   ↓                                 ↓
Order Placement          Strike + Greeks
```

### 3. Greeks Real-Time (CHANGED)
```
Current: Fetch from option_chain_cache.json (5-10s update)
New: Real-time from TWS (sub-second)
```

## File Changes Summary

### New Files (8 files, ~1000 lines)
- `broker_interface.py` - Abstract base
- `interactive_brokers.py` - IB implementation  
- `ib_contract_resolver.py` - Contract lookup
- `ib_greeks_calculator.py` - Greeks handling
- `broker_config.py` - Config management
- `market_detector.py` - Market detection
- Plus unit tests

### Modified Files (10 files, ~200 lines)
- `config.py` - Add broker selection
- `angelone.py` - Become adapter
- `monitor.py` - Add broker abstraction
- `webhook_parser.py` - Market-aware symbols
- `optmonitor.py` - IB Greeks handling
- Rest: Minimal changes (mostly adding market field)

### Reusable Code (Excellent!)
- ✅ 95% of exit logic
- ✅ 85% of alert validation
- ✅ 80% of position tracking
- ✅ 90% of logging framework
- ✅ 100% of ML signal filters

## Migration Options

### Option A: Parallel (RECOMMENDED)
```
Keep India bot 100% operational
Build US bot separately 
Test thoroughly in paper
Switch when ready
```
**Timeline:** 6 weeks | **Risk:** LOW | **Downtime:** 0

### Option B: Refactor-First
```
Refactor existing code for abstraction
Implement both brokers
Switch entirely
```
**Timeline:** 8-10 weeks | **Risk:** MEDIUM | **Downtime:** 1-2 weeks

## Critical Path Items

### Must Do First
1. ✅ Broker abstraction interface (Week 1)
2. ✅ IB basic order placement (Week 1)
3. ✅ Contract resolution system (Week 2)
4. ✅ Symbol mapping logic (Week 2)
5. ✅ Real-time Greeks integration (Week 3)
6. ✅ Paper trading validation (Week 4-5)

### Can Defer
- Advanced Greeks features
- Extended pre-market trading
- Complex options strategies
- Historical data integration

## Testing Checklist

### Phase 1: Unit Tests (Week 3)
- [ ] Broker interface tests (all methods)
- [ ] Symbol processing tests (US + Indian symbols)
- [ ] Market hours validation tests
- [ ] Contract resolution tests

### Phase 2: Integration Tests (Week 4)
- [ ] Single trade workflow
- [ ] Multiple concurrent positions
- [ ] Market hours transitions
- [ ] Greeks real-time updates
- [ ] Error scenarios (connection loss, etc)

### Phase 3: Stress Testing (Week 5)
- [ ] Rapid-fire alerts (20/min)
- [ ] Large position sizes
- [ ] Rate limiter behavior
- [ ] Data synchronization

### Phase 4: Live Validation (Week 6)
- [ ] Paper trading with real market data
- [ ] Pre-market, regular, after-hours behavior
- [ ] Commission calculations
- [ ] PnL tracking accuracy

## Cost Analysis

```
Development:
  Time: 180-200 hours
  Rate: $50-150/hour
  Total: $9,000 - $30,000

Infrastructure:
  IB Account: FREE (connect to existing)
  Paper Trading: FREE
  Monthly Ops: $0-20/month

Risk-Adjusted:
  Low Risk Success: 85-90%
  Payback period: 3-6 months of trading
```

## Known Gotchas ⚠️

1. **Contract ID Resolution**
   - IB requires conId for orders
   - Must resolve and cache at startup
   - Add 5-10s startup delay

2. **Greeks Data Async**
   - Arrive via callback, not immediate
   - Need threading queue synchronization
   - Budget 20 extra lines of code

3. **Market Hours Complexity**
   - 3 windows: pre-market, regular, after-hours
   - Different symbol eligibility per window
   - Need timezone conversions (IST vs ET)

4. **Options Multipliers**
   - SPX multiplier = 250 (not 100)
   - Must be config-driven
   - Major gotcha in P&L calculations

5. **TWS Connection**
   - Must keep TWS running
   - Connection can drop
   - Aggressive reconnect logic needed

## Success Metrics

| Metric | Target | Validation |
|--------|--------|-----------|
| Paper Orders | 100% filled | Track 50 test trades |
| Greeks Accuracy | Within 1% | Compare vs market |
| Position Tracking | 100% sync | Match IB statements |
| Monitoring Latency | < 5 seconds | Check during market hours |
| Alert Processing | 100% routed | Test all symbols |
| Market Hours | 100% correct | Test all 3 windows |

## Go/No-Go Decision Gate (End of Week 4)

### GO if:
- ✅ 50+ successful paper trades completed
- ✅ All Greeks validations passing
- ✅ Market hours correctly enforced
- ✅ No major bugs in integration tests
- ✅ Position sync with IB verified

### NO-GO if:
- ❌ Greeks data gaps causing exits
- ❌ Contract resolution too slow
- ❌ Market hours logic failing
- ❌ Rate limiting issues
- ❌ Insufficient confidence in integration

## Recommended Next Steps

1. **Week 1:** Set up development environment
   - Clone/branch codebase
   - Install ibapi library
   - Set up IB paper account connection

2. **Week 1:** Create broker abstraction
   - Design interface with team
   - Implement basic methods
   - Write unit tests

3. **Week 2:** Complete IB integration
   - Contract resolution
   - Order placement
   - Position queries

4. **Week 3:** Adapt signal processing
   - Update alert parsing
   - Add market detection
   - Modify monitoring

5. **Week 4:** Paper trading validation
   - Run 50+ test trades
   - Monitor all workflows
   - Collect metrics

6. **Week 5:** Go/No-Go decision
   - Review test results
   - Decide on live deployment
   - Plan next phases

---

## Questions to Answer Before Starting

1. **Do you have an Interactive Brokers account?**
   - If NO: Set up before starting development

2. **Can you run TWS (Trader Workstation) locally?**
   - If NO: Switch to IB Gateway (simpler)

3. **Do you want both India + US trading concurrently?**
   - If YES: Use Parallel approach (Option A)
   - If NO: Use Refactor approach (Option B)

4. **What's your risk tolerance for downtime?**
   - HIGH: Parallel development (6 weeks, no downtime)
   - MEDIUM: Sequential refactoring (8-10 weeks, 1-2 week downtime)

5. **Do you have 200+ hours to allocate?**
   - If NO: Consider vendor solution (Alpaca, Tastytrade)

---

**Last Updated:** 2024-12-14
**Confidence Level:** HIGH (based on code analysis)
**Recommendation:** PROCEED with phased approach

