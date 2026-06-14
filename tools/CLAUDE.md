# Trading Bots — Quick Reference (Updated 2026-06-14)

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
                           └─ optmonitor: 3s loop
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
