# Trading Bots — Full Context & Status (Updated 2026-06-27)

> **Purpose of this doc:** complete context for discussing the system with Claude (web/desktop).
> **Part 1** below = current status, strategy, LIVE-readiness, ratings, and the engineering done so far.
> **Part 2** (further down, "Operational Quick Reference") = architecture/ops details (ports, systemd, signal flow, env, cron).

---

# PART 1 — SYSTEM STATUS & DEEP CONTEXT

## 0. TL;DR
An automated **options-buying** system for Indian markets (NSE F&O) on the **AngelOne SmartAPI** broker. TradingView Pine scripts fire alerts → a webhook router fans them to **4 bot instances** that buy ATM±1 option contracts, manage them with layered stop-losses/trailing, and square off intraday. Currently **100% PAPER mode** (simulation with real market data). One shared AngelOne account (client `S635655`).

The 4 bots are two strategies × two moneyness:
- **CE_OPTIONS** (buy Calls) in **ITM** (:8080) and **OTM** (:8081) modes
- **PUT_OPTIONS** (buy Puts) in **ITM** (:8083) and **OTM** (:8082) modes

All share ONE codebase per side (`--mode ITM|OTM`), one AngelOne account, and run as systemd services on a small VPS.

## 1. Hardware / deployment reality (important for any perf discussion)
- **VPS: 2 vCPU, ~2–3.8 GB RAM** (hostname says `1vcpu-2gb`; `nproc`=2). This is the single biggest performance constraint.
- 4 Python bot processes + a webhook router + a live-PnL aggregator all share these 2 cores. Python GIL + 2 cores means CPU-bound work (e.g. parsing the instrument master, building option chains) **serializes** under concurrency.
- `instrument.json` (AngelOne token map) is **~36–39 MB / ~162k rows**, refreshed daily ~08:50.

## 2. Strategy & signal source
- **Signals come from TradingView Pine scripts** (`pinescripts/`), not from the bot. The bot is the *execution + risk-management* engine; the *edge* lives in the Pine signal.
  - CE entry types: `MOMENTUM_CONTINUATION`, `MACD_REVERSAL`, etc. PE: `TREND_CONTINUATION`, `PUT_*`.
  - Current Pine versions (per project memory): **CE ≈ v9.12, PUT ≈ v6.3**. **Pine changes require a manual TradingView redeploy** to go live — the running alerts may lag the repo's `.pine` files.
- Entry gating in the bot (after a signal arrives): signal validation → market-hours window (0930–1500 for NEUTRAL) → daily-limit → option-chain fetch → **PCR** (`putCallRatio`, cached 60s) + **IV** + **technical confirmation on 1-HOUR candles (RSI/MACD/MA)** → **ATM±offset strike selection** → **liquidity/spread gate** (hard reject if bid/ask spread > 5%) → order.
- Strike selection is by **strike vs spot** (not by greeks). Offsets: CE-ITM −1 (strike<spot), CE-OTM +1 (strike>spot), PE-ITM +1 (strike>spot=ITM for puts), PE-OTM −1 (strike<spot=OTM for puts). **Verified correct & isolated** across all 4 bots.
- Position sizing: `cap_per_trade` ≈ ₹30,000; daily trade limits; liquidity caps by OI/volume participation.

## 3. LIVE-READINESS STATUS (2026-06-27)

**Verdict: mechanically LIVE-ready (orders/SL/exits execute correctly), but the profit EDGE is UNPROVEN on clean data. Do not risk size until validated.**

### Ratings (retail-trader scale, /10)
| Factor | Score | Notes |
|---|---|---|
| Order infrastructure (entry/SL/modify/exit/EOD) | 8.5 | broker-managed SL, retries, reconciliation, orphan sweep |
| Latency engineering | 7.5 | 20s→2s fixed; bursts limited by 2 vCPU |
| Slippage handling | 7.0 | modeled + guards; **unvalidated on real fills** |
| Stability / resilience | 7.0 | strong guards + watchdog; thin hardware |
| Entry quality | 6.5 | solid gating, but only as good as the Pine signal |
| Exit logic | 6.5 | comprehensive — possibly *too many* overlapping early-exits |
| Risk/capital mgmt | 7.0 | per-trade cap, daily limits, liquidity sizing |
| Observability | 8.5 | per-stage timing, slippage logs, analyze_* tools |
| **Strategy / profit edge** | **4.0** | **UNPROVEN — trade history contaminated (see §8)** |
| **Overall** | **6.5–7** | above-average retail *engineering*; edge unverified |

### Before flipping to LIVE (in order)
1. **Redeploy Pine scripts** to TradingView so live signals match current logic.
2. **1–2 weeks clean PAPER** with slippage modeling ON → get real win-rate / profit-factor / avg-win:loss. Segregate the polluted `trades.csv` first.
3. **1-lot LIVE pilot** (smallest qty) → validate the slippage model + order lifecycle against real fills.
4. Then scale; consider **2→4–8 vCPU** if bursts matter.

## 4. Latency architecture (the big engineering story)
**Original problem:** alert→order latency was ~**20s flat all day** — likely the real cause of past LIVE slippage (price ran for 20s before the order landed). Root cause: the shared rate limiter pooled ALL broker endpoints into one 180/min (=3/s) budget, so cheap LTP/quote calls were throttled behind the candle endpoint.

**Fixes applied (CE + PUT mirrored):**
1. **Per-endpoint rate limiter** (`options_rate_limiter.py`): independent budgets per endpoint class — `ltp (10/s,490/min)`, `quote (10/s,490/min)`, `candle (3/s,178/min)`, `order (19/s,490/min)`, all below AngelOne's documented caps. State in `/tmp/angelone_options_shared_rate_limit_v2.json` (file-locked, shared across the 4 processes). Result: `rl_wait` → 0. **Solo alert→order: ~20s → ~2–4s.**
2. **Instrument master parsed ONCE per process** (`ce_extractor.py`/`pe_extractor.py`): `InstrumentCEExtractor()` used to `json.load()` the 36 MB file **per alert** (~23×/burst) → OOM + GIL-bound CPU inflation. Now cached process-wide by file mtime, shared read-only, auto-reload on daily refresh. **chain+filter 7-17s → 0.5-1.6s; 20-burst 52s → 25s.**
3. **Per-bot alert concurrency semaphore** (`optapi.py`, `OPTIONS_ALERT_CONCURRENCY=4`): webhook returns 202 instantly + spawns a thread per alert, but only 4 do broker-heavy work at once → prevents OOM on 2 GB. Threads **wait, never drop**.
4. **Shared cross-process candle cache** (`/tmp/angelone_options_candle_cache.json`, 300s TTL, async writes): the 4 bots no longer each fetch the same underlying's 1-HOUR candles. Only feeds indicator confirmation; **strike & entry premium are always fetched FRESH**.
5. **Removed redundant OI call**: liquidity check did `get_market_data` + `get_oi_data`, but `get_market_data` (getMarketData FULL) already returns OI+volume → dropped the duplicate.
6. **Light option chain**: greeks computed for the *selected* contract only; LTP fetch narrowed to ±strikes around ATM; sector enrichment moved to a background thread (off the entry critical path).

**Current latency (weekend = worst case; LIVE expected faster):**
- Solo warm: ~4s. Realistic **5–6 burst: ~10–13s**. **20 cold alerts to one bot: ~25s** (5 waves of 4).
- The remaining cost is **per-alert broker round-trips (~4s) × 2-vCPU/GIL contention**, NOT our limiter (`rl_wait=0`). "20 in 10s" needs more vCPUs.
- Weekend numbers are pessimistic: on a holiday, volume=0 triggers a liquidity REFETCH (`sleep(0.25)`+call) that won't fire in LIVE.

## 5. Slippage handling
- **PAPER slippage modeling ON** (`OPTIONS_PAPER_SLIPPAGE_MODELING=true`): books entry at real **ask**, exit at real **bid** → PAPER PnL ≈ LIVE. Logs `SLIPPAGE_ENTRY` / `SLIPPAGE_EXIT` with ideal vs fill vs %.
- **Hard spread reject** at 5% (`REJECTED_LIQUIDITY`) + OI/volume participation caps. Advisory spread gate separate (`OPTIONS_MAX_ENTRY_SPREAD_PCT`, enforce=false).
- **Gap:** never validated against a *real* broker fill. The 1-lot pilot is to confirm the model.
- LIVE-only logging now added: `BUY_CONFIRMATION ... exec_slippage% / ltp_slippage% / confirm_ms` and a `BUY_FILL_SLIPPAGE` event (real fill vs intended price vs decision LTP).

## 6. Entry / Monitor / Modify / Exit lifecycle (audited LIVE-ready)
- **Entry:** correct strike isolation, fresh prices, multi-gate filter, zero dropped alerts under stress (40/40, 20/20 complete).
- **Monitor loop:** every **2s**; bulk LTP via `get_ltp_bulk` (50/call); **no-stale-price guard** (skips exits if 0 valid LTPs); per-position assignment keyed by symbol (no cross-contamination). Cadence holds ~2s at low load but stretches to ~7s under a concurrent entry burst (2-core contention) — exits *delayed, not missed*. New `MONITOR_CYCLE: cycle_ms` log tracks this.
- **Modify / TRIAL_SL:** verified live-trailing; **instant** (no rate-limit wait, no fetch — local token lookup); adaptive (skips <1% change), ~1s cooldown, safety-ceiling clamp; on LIVE updates `sl_order_id` to AngelOne's new id so cancel hits the right order; skips during close.
- **Exit:** single guarded path. **Double-close guard** (`_closing_lock`) prevents concurrent close→naked short. Order: **cancel SL (3 retries; handles SL-fired-during-cancel) → MARKET SELL (5-retry backoff) → wait-for-fill** (unconfirmed → keeps position open, no false close). PAPER models exit slippage. 75 exits audited today: 0 double-closes, 0 orphan SLs, 0 errors.
- **Fixes from the lifecycle audits:** EOD broker-orphan sweep, partial-fill qty guard, modify qty=0 guard, and **exit greeks capture moved off the critical path** (cheap fallback up-front; live `fetch_option_chain` greeks captured AFTER the SELL, just before PnL booking).

## 7. TRIAL_SL post-exit telemetry
- After a position exits, `live_data_tracker.py` registers a **post-exit watcher** that polls the contract's LTP (every 5s for 5 min) and records `max_after_exit` / `min_after_exit` + checkpoints (30/120/300s) **alongside the trail config used** (`trail_profile`, `trail_activation_threshold`, `trailing_gap`) and regime. Output: `data/trial_sl_post_exit_results.jsonl` (hundreds–thousands of rows already). This is the data for tuning TRIAL_SL ("did we exit before the real peak?"). Example: SUNPHARMA exit ₹3.65 but `max_after_exit` ₹4.15 → trail too tight there.
- Efficiency: the post-exit LTP poll is now **batched via `get_ltp_bulk` chunked at 50** (was 1 `get_market_data` per symbol) — matters during EOD/stale-exit waves. Persisted to `trial_sl_post_exit_watchers.json` (survives restarts), runs off the trading path.

## 8. ⚠️ PERFORMANCE DATA CAVEAT (read before judging profitability)
The per-bot `data/trades.csv` files are **contaminated** and CANNOT be used to judge the strategy:
- CE bots show ~4000+ rows over 114+ days with **impossible values** (one row −₹66,930,000; 100+ trades/day) — a mix of **generated ML training data + backtests + today's synthetic burst-tests**.
- The only clean-looking real sample is **PE-OTM (~109 trades, Apr–Jun 2026, profit factor 1.49, 52% win)** — mildly positive but a small sample.
- **Net: there is no trustworthy PAPER performance number yet.** Getting one (clean 1–2 week PAPER run) is the key gate before LIVE.

## 9. Observability for the LIVE day (what to grep / run)
- **Latency:** `alerts.jsonl` (per-status timestamps) · `ALERT_TIMING: sem_wait_ms/proc_ms/total_ms` · `ENTRY_TIMING: chain+filter/sel+liq/pricing+order/rl_wait` · `BUY_CONFIRMATION ... confirm_ms` (LIVE order→fill) · `MONITOR_CYCLE: cycle_ms` · tool `tools/analyze_latency.py`.
- **Slippage:** `SLIPPAGE_ENTRY` / `SLIPPAGE_EXIT` (modeled) · `BUY_FILL_SLIPPAGE` (real LIVE fill vs intended/decision price) · tool `tools/analyze_slippage.py`.
- Other analysis tools in `*/tools/`: `analyze_stale_impact.py`, `analyze_trial_sl_5pct.py`, `check_ltp_rebound.py`, `stale_cost_analysis.py`, `analyze_worst_symbols.py`.

## 10. How to switch PAPER → LIVE
- Flip `TRADING_MODE=PAPER` → `LIVE` in `CE_OPTIONS/tools/.env` and `PUT_OPTIONS/tools/.env`, restart the 4 services. `PAPER_TRADING_ENABLED` derives from this. In LIVE the real `placeOrder`/`modifyOrder`/SL/`wait_for_*_confirmation` paths activate (these are bypassed in PAPER).
- Order APIs (place/modify/cancel) use separate AngelOne endpoints and **bypass the market-data rate limiter** → never blocked by quote saturation.

## 11. Open items / candidate next work
- (Edge) Clean PAPER measurement + Pine redeploy + 1-lot LIVE pilot (the gate).
- (Perf) 2→4–8 vCPU is the single biggest lever for burst latency; optionally drop the spot-LTP call / reuse the chain quote to shave `sel+liq`.
- (Possibly) Exit logic has many overlapping early-exits (stale-consolidation, dead-trade-5min, momentum-reversal, MACD-fade) that cut trades at small losses — worth reviewing whether they help or hurt once clean PAPER data exists.

> **Project memory** lives in `/root/.claude/projects/-root-santhosh-trading/memory/` (MEMORY.md index + per-topic files: latency-fix, burst-throughput, monitor-exit-audit, order-lifecycle-audit, slippage-modeling, otm-itm-capital-sizing, ce-pine-status). Git branch: `trading_refactored` (remote `github.com/santhoshb42/trading_equity_options`).

---

# PART 2 — OPERATIONAL QUICK REFERENCE

## Repository Layout

```
trading/
├── CE_OPTIONS/              # CE (Call) bot — OTM + ITM modes in one codebase
├── PUT_OPTIONS/             # PE (Put) bot — OTM + ITM modes in one codebase
├── equity/                  # Equity (stock) bot
├── tools/                   # Shared infrastructure (this file lives here)
│   ├── CLAUDE.md            # ← you are here
│   ├── REVISIT.md           # Deferred ideas and future work
│   ├── live_pnl_aggregator.py   # Cross-bot live P&L daemon
│   ├── eod_backup_handler.py    # EOD backup + clear (imported by all bots)
│   └── deployment/
│       └── live-pnl-aggregator.service
├── live_pnl/                # Output of live_pnl_aggregator — one file per bot + summary
├── webhook_router.py        # Port 80; routes TradingView alerts to bots
├── alert_system.py          # Shared alerting (Telegram/email)
├── pinescripts/             # TradingView Pine Script source files
└── backup/                  # Old bot dirs (pre-refactor), safe to delete after stable
```

---

## Bots and Ports

| Bot      | Directory       | Port | Type | Strike logic        | Accepts         | Systemd service  | SyslogIdentifier |
|----------|-----------------|------|------|---------------------|-----------------|------------------|------------------|
| CE OTM   | `CE_OPTIONS/`   | 8081 | CE   | OTM (offset +1)     | BUY             | `ce-otm.service` | `ce-otm-bot`     |
| CE ITM   | `CE_OPTIONS/`   | 8080 | CE   | ITM (offset -1)     | BUY             | `ce-itm.service` | `ce-itm-bot`     |
| PE OTM   | `PUT_OPTIONS/`  | 8082 | PE   | OTM put (offset -1) | SELL / BUY_PUT  | `pe-otm.service` | `pe-otm-bot`     |
| PE ITM   | `PUT_OPTIONS/`  | 8083 | PE   | ITM put (offset +1) | SELL / BUY_PUT  | `pe-itm.service` | `pe-itm-bot`     |
| Equity   | `equity/`       | 8090 | Stock| —                  | BUY / SELL      | `eqbot.service`  | —                |
| Router   | `webhook_router.py` | 80 | — | —                 | TradingView POST| `webhook-router.service` | —        |

> **Strike offset signs:** CE — OTM means offset +1 (higher strike), ITM means -1 (lower). PE — OTM means offset -1 (lower strike, below spot = OTM for puts), ITM means +1 (higher strike, above spot = ITM for puts).

---

## Key Architectural Pattern: Dual-Mode Shared Codebase

CE and PE bots each have **one codebase, two runtime instances**. The `--mode` flag (OTM or ITM) is parsed before any imports, sets `BOT_MODE` in env, and all config/data paths derive from it.

```
CE_OPTIONS/
├── main.py          # single entry point; --mode OTM → 8081, --mode ITM → 8080
├── optcode/         # shared optcode (one copy, used by both instances)
├── OTM/
│   ├── data/        # OTM runtime data (positions, pnl, session, live_data*)
│   └── logs/        # OTM logs
├── ITM/
│   ├── data/        # ITM runtime data (isolated)
│   └── logs/        # ITM logs
├── aggregate_live_data.py   # daemon: merges OTM+ITM → CE_OPTIONS/live_data.json
├── live_data.json           # merged CE view (updated every 10s)
├── live_data_trades.csv     # merged CE trades (updated every 10s)
├── deployment/
│   ├── ce-otm.service
│   └── ce-itm.service
└── tools/
    ├── .env                 # shared credentials (ANGEL_API_KEY etc.)
    ├── .env.otm             # OPTIONS_WEBHOOK_PORT=8081, OPTIONS_STRIKE_OFFSET=1
    ├── .env.itm             # OPTIONS_WEBHOOK_PORT=8080, OPTIONS_STRIKE_OFFSET=-1
    ├── instrument.json      # AngelOne token map (refreshed daily at 8:50 AM)
    ├── fetch_nfo_instruments.py
    ├── run_eod_learning.sh
    ├── setup_systemd.sh
    ├── setup_cron.sh
    └── setup_eod_learning_cron.sh
```

PUT_OPTIONS mirrors this layout exactly, with PE-specific differences noted below.

---

## optconfig.py — Key Pattern

```python
BASE_DIR = Path(__file__).parent.parent          # CE_OPTIONS/ or PUT_OPTIONS/
BOT_MODE = os.getenv("BOT_MODE", "OTM").upper()  # set before import via argparse
DATA_DIR = BASE_DIR / BOT_MODE / "data"           # CE_OPTIONS/OTM/data/ or ITM/data/
LOGS_DIR = BASE_DIR / BOT_MODE / "logs"

# Env loading: shared creds first, then mode overrides (PORT, STRIKE_OFFSET)
load_dotenv(BASE_DIR / "tools" / ".env")
load_dotenv(BASE_DIR / "tools" / f".env.{BOT_MODE.lower()}", override=True)
```

All runtime files (positions.json, session.json, live_data.json, pnl_history, etc.) resolve through `DATA_DIR`, so OTM and ITM are fully isolated.

---

## main.py — Startup Pattern

```python
import argparse
_arg_parser = argparse.ArgumentParser(add_help=False)
_arg_parser.add_argument('--mode', choices=['OTM', 'ITM'], default='OTM')
_mode_args, _ = _arg_parser.parse_known_args()
os.environ['BOT_MODE'] = _mode_args.mode.upper()   # must be set BEFORE any optcode imports

# sys.path for CE_OPTIONS/main.py:
sys.path.insert(0, str(Path(__file__).parent))          # CE_OPTIONS/
sys.path.insert(0, str(Path(__file__).parent.parent))   # trading/
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))  # trading/tools/

# PID and lock files go in mode-specific subdirectory:
_mode_dir  = script_dir / _bot_mode.upper()             # CE_OPTIONS/OTM/ or CE_OPTIONS/ITM/
pid_file   = _mode_dir / f"{_bot_mode.lower()}_bot.pid"
lock_file  = _mode_dir / f".{_bot_mode.lower()}_bot.lock"
```

For `PUT_OPTIONS/OTM/main.py` and `PUT_OPTIONS/ITM/main.py` (legacy sub-dir launchers), sys.path also includes `parent.parent` (PUT_OPTIONS/) for optcode access.

---

## Systemd Services

### Service files location
- CE bots: `CE_OPTIONS/deployment/ce-otm.service`, `ce-itm.service`
- PE bots: `PUT_OPTIONS/deployment/pe-otm.service`, `pe-itm.service`
- Aggregator: `tools/deployment/live-pnl-aggregator.service`
- All installed to `/etc/systemd/system/`

### Key service attributes (all 4 bots)
```ini
Environment=BOT_MODE=OTM          # or ITM
EnvironmentFile=-/path/to/tools/.env
EnvironmentFile=-/path/to/tools/.env.otm   # overrides PORT + STRIKE_OFFSET
ExecStart=/usr/bin/python3 /root/santhosh/trading/CE_OPTIONS/main.py --mode OTM
Restart=always
RestartSec=10
StartLimitBurst=10 / StartLimitInterval=300
```

### Status check
```bash
systemctl status ce-otm ce-itm pe-otm pe-itm live-pnl-aggregator --no-pager
# All should show: active (running) + Loaded: enabled
```

### Logs
```bash
journalctl -u ce-otm-bot -f          # CE OTM live
journalctl -u ce-itm-bot -f          # CE ITM live
journalctl -u pe-otm-bot -f          # PE OTM live
journalctl -u pe-itm-bot -f          # PE ITM live
journalctl -u live-pnl-agg -f        # Aggregator
```

---

## Live P&L Aggregation (Two Levels)

### Level 1 — Per-bot merged view (inside each bot process)
`CE_OPTIONS/aggregate_live_data.py` and `PUT_OPTIONS/aggregate_live_data.py` run as daemon threads every 10 seconds, merging OTM+ITM data into the parent directory:

```
CE_OPTIONS/live_data.json           ← OTM + ITM merged
CE_OPTIONS/live_data_trades.csv     ← OTM + ITM merged
PUT_OPTIONS/live_data.json
PUT_OPTIONS/live_data_trades.csv
```

### Level 2 — Cross-bot summary (standalone service)
`tools/live_pnl_aggregator.py` runs as `live-pnl-aggregator.service` every 5 seconds, copying all 4 bots' data into `live_pnl/`:

```
live_pnl/
├── OTM_CE_live_data.json
├── OTM_CE_live_data_trades.csv
├── ITM_CE_live_data.json
├── ITM_CE_live_data_trades.csv
├── OTM_PUT_live_data.json
├── OTM_PUT_live_data_trades.csv
├── ITM_PUT_live_data.json
├── ITM_PUT_live_data_trades.csv
└── summary.json                    ← combined P&L across all 4 bots
```

`summary.json` top-level keys: `bots_online`, `bots_with_positions`, `combined.total_ongoing_positions`, `combined.total_pnl`, `combined.total_unrealized_pnl`, `combined.total_realized_pnl`, and a `bots` dict with per-bot breakdown.

---

## Webhook Router (`webhook_router.py`)

Port 80. Receives TradingView POST, detects CE vs PE, routes in parallel threads.

**Detection priority:**
1. Wrapper key `PE_Alerts` → PE; `Alerts` → CE
2. `option_side=PE` or `original_action=BUY_PUT` → PE
3. `action=BUY_PUT` or `entry_type` starts with `PUT_` → PE
4. `action=BUY` → CE
5. Symbol suffix `CE` / `PE`
6. Default → CE

**Routing:**
- CE alert → 8081 (ce-otm) + 8080 (ce-itm) in parallel threads
- PE alert → 8082 (pe-otm) + 8083 (pe-itm) in parallel threads
- Equity alert → 8090

**Payload must include `symbol` field at top level** (router rejects without it).

---

## CE vs PE Differences

| Aspect                  | CE bots (`CE_OPTIONS/`)              | PE bots (`PUT_OPTIONS/`)                   |
|-------------------------|--------------------------------------|--------------------------------------------|
| Extractor               | `ce_extractor.py`                    | `pe_extractor.py`                          |
| Signal validator        | Rejects SELL                         | Rejects BUY                                |
| Execution               | BUY CE contract                      | BUY PE contract                            |
| Endpoint                | `/webhook/options`                   | `/webhook/put_options`                     |
| Port env var            | `OPTIONS_WEBHOOK_PORT`               | `PUT_OPTIONS_WEBHOOK_PORT`                 |
| OTM strike offset       | +1 (strike above spot)               | -1 (strike below spot = OTM for put)       |
| ITM strike offset       | -1 (strike below spot)               | +1 (strike above spot = ITM for put)       |
| Candle direction gate   | UP (bullish)                         | DOWN (bearish)                             |
| Confidence attributes   | Standard MLConfig                    | Extra: `MIN_CONFIDENCE_PRE_FALL`, `MIN_CONFIDENCE_PULLUP`, `MIN_CONFIDENCE_MACD_BREAKDOWN`, etc. |

---

## optcode/ Module Map

All bots share an identical optcode layout:

| File | Role |
|------|------|
| `optconfig.py` | All config: BOT_MODE, DATA_DIR, ports, capitals, Greeks, env loading |
| `optapi.py` | Flask webhook server; queues signals for async processing |
| `optsignalvalidator.py` | Validates TradingView signal; CE rejects SELL, PE rejects BUY |
| `angelone_options.py` | AngelOne SmartAPI integration; calls ce/pe_extractor |
| `ce_extractor.py` / `pe_extractor.py` | Instrument token lookup for strike selection |
| `optmonitor.py` | Position monitor loop: Greeks, hard SL, TP, trailing stop, stale check |
| `entry_filter_engine.py` | Gate: time window, capital limits, IV percentile, market trend |
| `market_sentiment.py` | PCR, OI buildup analysis |
| `technical_analyzer.py` | RSI, VWAP, EMA indicators |
| `live_data_tracker.py` | Tracks open positions in-memory |
| `live_data_updater.py` | Writes live_data.json every few seconds |
| `live_data_table_formatter.py` | Renders live_data_trades.csv human-readable table |
| `csv_updater.py` | Updates live_data_trades.csv |
| `eod_learning_aggregator.py` | EOD aggregation and archival |
| `options_learning_engine.py` | Symbol performance tracking (ML) |
| `ml_signal_scorer.py` | ML scoring for signals |
| `opt_hybrid_learning_engine.py` | Hybrid ML engine |
| `instrument_manager.py` | Loads/refreshes instrument.json token map |
| `optlogging.py` | Logging setup (writes to LOGS_DIR) |

---

## Shared Modules in `trading/tools/`

| File | Role | How imported |
|------|------|--------------|
| `eod_backup_handler.py` | EOD backup + clear of live data files | `from eod_backup_handler import run_eod_backup` (resolved via sys.path to trading/tools/) |
| `live_pnl_aggregator.py` | Cross-bot live P&L daemon | standalone service; `TRADING_DIR = Path(__file__).parent.parent` |
| `alert_system.py` | Telegram/email alerts | at trading/ root, on sys.path |

---

## Cron Schedule (Mon–Fri)

| Time | Action |
|------|--------|
| 08:45 | System reboot (5-min delay) |
| 08:50 | Instrument refresh: `CE_OPTIONS/tools/fetch_nfo_instruments.py`, then copy to PUT_OPTIONS/tools/ |
| 08:55 | Ensure webhook-router.service is active |
| 09:00 | Ensure ce-otm, ce-itm, pe-otm, pe-itm, live-pnl-aggregator are active |
| Every 30min 09:00–16:00 | Health-check all 4 bots; restart if inactive |
| 15:12 | Square-off: POST to /square-off on 8081, 8080, 8082, 8083 |
| 15:30 | EOD learning: CE OTM + ITM, PE OTM + ITM (via run_eod_learning.sh) |
| Sunday 00:00 | Log cleanup: remove log dirs older than 7 days |

---

## Key Config Knobs (`optconfig.py`)

| Class | Key Variables |
|-------|---------------|
| `AngelOneConfig` | `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PASSWORD`, `ANGEL_TOTP_KEY` (from `.env`) |
| `OptionsTradingConfig` | `STRIKE_OFFSET`, `SL_PERCENT`, `TP_PERCENT`, `TRAILING_STOP_*` |
| `WebhookConfig` | `PORT`, `ENDPOINT` |
| `OptionsCapitalConfig` | `MAX_CAPITAL_PER_TRADE`, daily limits, per-symbol limits |
| `MLConfig` | Delta targets, Greeks tolerances, `ML_CONFIDENCE_THRESHOLD` |
| `PCROIConfig` | PCR entry/exit thresholds, OI buildup minimum |
| `MonitoringConfig` | Position check interval, Greeks refresh interval |

---

## Ops Runbook

```bash
# Status at a glance
systemctl status ce-otm ce-itm pe-otm pe-itm live-pnl-aggregator --no-pager

# Health endpoints
curl http://127.0.0.1:8081/health   # CE OTM
curl http://127.0.0.1:8080/health   # CE ITM
curl http://127.0.0.1:8082/health   # PE OTM
curl http://127.0.0.1:8083/health   # PE ITM

# Router stats
curl http://127.0.0.1:80/stats

# Send test alert (CE)
curl -s -X POST http://localhost:80/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BANKNIFTY","action":"BUY","confidence":95,"entry_type":"CALL_BUY"}'

# Send test alert (PE)
curl -s -X POST http://localhost:80/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BANKNIFTY","action":"SELL","confidence":95,"entry_type":"PUT_BUY"}'

# Live P&L summary
cat /root/santhosh/trading/live_pnl/summary.json | python3 -m json.tool

# Restart all
systemctl restart ce-otm ce-itm pe-otm pe-itm live-pnl-aggregator

# Deploy updated service file for CE bots
cp CE_OPTIONS/deployment/ce-otm.service /etc/systemd/system/
cp CE_OPTIONS/deployment/ce-itm.service /etc/systemd/system/
systemctl daemon-reload && systemctl restart ce-otm ce-itm

# Deploy updated service file for PE bots
cp PUT_OPTIONS/deployment/pe-otm.service /etc/systemd/system/
cp PUT_OPTIONS/deployment/pe-itm.service /etc/systemd/system/
systemctl daemon-reload && systemctl restart pe-otm pe-itm

# Deploy live-pnl-aggregator service
cp tools/deployment/live-pnl-aggregator.service /etc/systemd/system/
systemctl daemon-reload && systemctl restart live-pnl-aggregator

# Manual EOD learning (one bot)
BOT_MODE=OTM /root/santhosh/trading/CE_OPTIONS/tools/run_eod_learning.sh OTM
```

---

## Environment Variables (per bot `.env`)

```env
# trading/CE_OPTIONS/tools/.env  (same structure for PUT_OPTIONS/tools/.env)
ANGEL_API_KEY=
ANGEL_CLIENT_CODE=
ANGEL_PASSWORD=
ANGEL_TOTP_KEY=

# Common trading params (can be overridden per mode)
OPTIONS_MAX_CAPITAL=10000
OPTIONS_MAX_DAILY_TRADES=5
OPTIONS_IV_PERCENTILE_MIN=30
OPTIONS_IV_PERCENTILE_MAX=90
OPTIONS_SL_PERCENT=10
OPTIONS_TP_PERCENT=30
```

```env
# tools/.env.otm  (CE OTM overrides)
OPTIONS_WEBHOOK_PORT=8081
OPTIONS_STRIKE_OFFSET=1

# tools/.env.itm  (CE ITM overrides)
OPTIONS_WEBHOOK_PORT=8080
OPTIONS_STRIKE_OFFSET=-1

# PUT_OPTIONS/tools/.env.otm  (PE OTM overrides)
PUT_OPTIONS_WEBHOOK_PORT=8082
OPTIONS_STRIKE_OFFSET=-1

# PUT_OPTIONS/tools/.env.itm  (PE ITM overrides)
PUT_OPTIONS_WEBHOOK_PORT=8083
OPTIONS_STRIKE_OFFSET=1
```

---

## Data Directory Layout (per mode)

```
CE_OPTIONS/OTM/data/
├── positions.json            # open positions (active AngelOne orders)
├── session.json              # AngelOne auth session cache
├── option_positions.json     # detailed option positions
├── option_pnl_history.json   # P&L history (closed trades)
├── live_data.json            # live snapshot (updated every 3s)
├── live_data_trades.csv      # human-readable trade table
├── option_chain_cache.json   # option chain cache
├── alert_recovery_queue.jsonl
├── liquidity_decisions.jsonl
├── daily_trades_YYYY-MM-DD.json
├── learning/
│   └── symbol_stats.json     # ML symbol performance stats
└── archive/
    └── option_pnl_history_YYYY-MM-DD_*.json
```

---

## Signal Flow (full path)

```
TradingView POST /webhook (port 80, webhook_router.py)
  └─ detect_alert_type() → CE or PE
       ├─ CE → forward to 8081 + 8080 simultaneously (threads)
       └─ PE → forward to 8082 + 8083 simultaneously (threads)

Bot receives POST /webhook/options or /webhook/put_options
  └─ optapi.py: validates payload, queues signal
       └─ WEBHOOK_WORKER thread dequeues
            └─ optsignalvalidator.validate_options_signal()
                 ├─ entry_filter_engine: time gate, capital gate, IV gate, market trend
                 └─ angelone_options: strike selection via ce/pe_extractor
                      └─ place BUY order on AngelOne SmartAPI
                           └─ optmonitor: 2s loop (pinned; see Part 1 §6)
                                ├─ refresh LTP
                                ├─ hard SL check
                                ├─ trailing stop (trial SL, then active trailing)
                                ├─ TP check
                                ├─ stale consolidation exit
                                └─ IV crash exit
```

---

## PID and Lock Files

| Bot | PID file | Lock file |
|-----|----------|-----------|
| CE OTM | `CE_OPTIONS/OTM/otm_bot.pid` | `CE_OPTIONS/OTM/.otm_bot.lock` |
| CE ITM | `CE_OPTIONS/ITM/itm_bot.pid` | `CE_OPTIONS/ITM/.itm_bot.lock` |
| PE OTM | `PUT_OPTIONS/OTM/otm_bot.pid` | `PUT_OPTIONS/OTM/.otm_bot.lock` |
| PE ITM | `PUT_OPTIONS/ITM/itm_bot.pid` | `PUT_OPTIONS/ITM/.itm_bot.lock` |
