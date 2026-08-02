"""
Market Regime Daemon — trading/tools/market_regime_daemon.py

Single writer, shared by all 4 bots. Computes NIFTY's own trend-efficiency
regime (GOOD/NEUTRAL/BAD) from real 1-min candles and publishes it to a
shared file so the existing (currently no-op) MARKET_TREND_GATE in each
bot's optapi.py can log real regime data instead of always defaulting to
NEUTRAL. GOOD/BAD/NEUTRAL capital caps are currently equal (see
optconfig.py OptionsCapitalConfig) — this is study-only telemetry for now,
not a live filter.

Refresh cadence: checks every POLL_SECONDS whether a new NIFTY 1-min candle
has closed; only recomputes on a genuinely new bar, so freshness is bounded
by the same 1-min resolution the bots already trade on, without hammering
the broker (one shared computation instead of 4 redundant per-bot polls).

Efficiency = |net move| / sum(|bar-to-bar moves|) over a trailing window.
High efficiency + up  -> GOOD (clean trend day, breakout follow-through likely)
High efficiency + down -> BAD
Low efficiency (either direction) -> NEUTRAL (chop — the 07-14 -44K session
measured 2%; the 07-13 +10K session measured 20%; threshold set at the
midpoint pending more days of evidence).

BAD-regime recovery check (2026-07-14, user observation): 07-13 opened BAD and
recovered to NEUTRAL -> good CE day; 07-14 went NEUTRAL -> BAD and kept
worsening -> bad CE day. So a BAD label alone doesn't distinguish "bottoming"
from "still falling." A short SHORT_WINDOW_MINUTES momentum check layered on
top of the main WINDOW_MINUTES regime answers that: recovering = the recent
short-window net move is positive even while the longer window is still
net-negative (BAD). entry_advice=BLOCK only when BAD and NOT recovering — a
live PAPER gate as of 2026-07-14 (bot-side, optapi.py). NEUTRAL/GOOD always
ALLOW (user: "in NEUTRAL some ups and downs are fine").

Outputs:
  tools/market_regime.json          — latest snapshot (read live by bots)
  tools/market_regime_history.jsonl — full-day timeline (for later study)
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

TRADING_DIR = Path(__file__).parent.parent
CE_DIR = TRADING_DIR / "CE_OPTIONS"
SNAPSHOT_FILE = TRADING_DIR / "tools" / "market_regime.json"
HISTORY_FILE = TRADING_DIR / "tools" / "market_regime_history.jsonl"

POLL_SECONDS = 20
WINDOW_MINUTES = 15
EFFICIENCY_THRESHOLD = 12.0  # % — midpoint between 07-14 chop (2%) and 07-13 trend (20%)
SHORT_WINDOW_MINUTES = 5
RECOVERY_THRESHOLD_PCT = 0.02  # short-window net move must clear this to count as "recovering"
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# H13.1: session-cumulative health (from 09:30 to now, not a trailing window).
# 20-day evidence (H12b): per-signal gate eff>=8% AND net>+0.05% split trades into
# +9.5% (healthy) vs -9.4% (weak), but only 8/18 healthy days were positive ->
# this drives position SIZING in the bots, never entry blocking.
SESSION_START = dtime(9, 30)
SESSION_EFF_HEALTHY = 8.0    # % efficiency since 09:30
SESSION_NET_HEALTHY = 0.05   # % net move since 09:30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("market_regime")

sys.path.insert(0, str(CE_DIR))

_stop_event = False


def _signal_handler(signum, frame):
    global _stop_event
    logger.info(f"Received signal {signum}, shutting down")
    _stop_event = True


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def _authenticate_broker():
    from dotenv import load_dotenv
    load_dotenv(CE_DIR / "tools" / ".env")
    os.environ.setdefault("BOT_MODE", "OTM")
    from optcode.angelone_options import AngelOneOptionsBroker
    broker = AngelOneOptionsBroker()
    broker.authenticate()
    return broker


def _classify(efficiency_pct: float, net_move_pct: float) -> str:
    if efficiency_pct >= EFFICIENCY_THRESHOLD:
        return "GOOD" if net_move_pct > 0 else "BAD"
    return "NEUTRAL"


def _net_move_pct(window):
    op = window[0]["open"]
    cl = window[-1]["close"]
    if op == 0:
        return None
    return (cl - op) / op * 100


def _compute_session(candles):
    """H13.1: cumulative efficiency + net move from today's 09:30 to the latest bar.
    Unlike the trailing 15-min regime window, this is the day-quality signal —
    it only strengthens or weakens gradually, so it's stable enough for sizing."""
    today = datetime.now().strftime("%Y-%m-%d")
    rows = [c for c in candles
            if c["timestamp"][:10] == today and c["timestamp"][11:16] >= "09:30"]
    # prev-day close (gap-inclusive day change) — a gap-down day looks flat to
    # session_net (measured from 09:30 open) but is genuinely bearish. day_net_pct
    # captures the gap so the PUT bearish arm doesn't miss gap-down selloff days.
    prev_rows = [c for c in candles if c["timestamp"][:10] < today]
    prev_close = prev_rows[-1]["close"] if prev_rows else None
    if len(rows) < 3:
        return {"session_eff_pct": None, "session_net_pct": None,
                "day_net_pct": None, "session_health": "NA"}
    op = rows[0]["open"]
    cl = rows[-1]["close"]
    path = sum(abs(c["close"] - c["open"]) for c in rows)
    if op == 0 or path == 0:
        return {"session_eff_pct": None, "session_net_pct": None,
                "day_net_pct": None, "session_health": "NA"}
    net = (cl - op) / op * 100
    eff = abs(cl - op) / path * 100
    day_net = (cl - prev_close) / prev_close * 100 if prev_close else net
    healthy = eff >= SESSION_EFF_HEALTHY and net > SESSION_NET_HEALTHY
    return {
        "session_eff_pct": round(eff, 2),
        "session_net_pct": round(net, 3),
        "day_net_pct": round(day_net, 3),
        "session_health": "HEALTHY" if healthy else "WEAK",
    }


def _compute_regime(candles):
    """candles: list of dicts with open/high/low/close/timestamp, oldest first."""
    window = candles[-WINDOW_MINUTES:]
    if len(window) < 3:
        return None
    op = window[0]["open"]
    cl = window[-1]["close"]
    path = sum(abs(c["close"] - c["open"]) for c in window)
    if op == 0 or path == 0:
        return None
    net_move_pct = _net_move_pct(window)
    efficiency_pct = abs(cl - op) / path * 100
    trend = _classify(efficiency_pct, net_move_pct)

    short_window = candles[-SHORT_WINDOW_MINUTES:]
    short_net_move_pct = _net_move_pct(short_window) if len(short_window) >= 3 else None
    recovering = short_net_move_pct is not None and short_net_move_pct > RECOVERY_THRESHOLD_PCT

    if trend == "BAD":
        entry_advice = "ALLOW" if recovering else "BLOCK"
    else:
        entry_advice = "ALLOW"

    return {
        "market_trend": trend,
        "efficiency_pct": round(efficiency_pct, 2),
        "net_move_pct": round(net_move_pct, 3),
        "window_minutes": len(window),
        "short_window_minutes": len(short_window),
        "short_window_net_move_pct": round(short_net_move_pct, 3) if short_net_move_pct is not None else None,
        "recovering": recovering,
        "entry_advice": entry_advice,
        **_compute_session(candles),
        "last_candle_ts": window[-1]["timestamp"],
        "computed_at": datetime.now().astimezone().isoformat(),
    }


def _write_snapshot(regime: dict) -> None:
    tmp = SNAPSHOT_FILE.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        json.dump(regime, fh)
    tmp.replace(SNAPSHOT_FILE)
    with open(HISTORY_FILE, "a") as fh:
        fh.write(json.dumps(regime) + "\n")


def _in_market_hours() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def main():
    logger.info(f"Market regime daemon starting | poll={POLL_SECONDS}s window={WINDOW_MINUTES}m "
                f"threshold={EFFICIENCY_THRESHOLD}%")
    broker = _authenticate_broker()
    logger.info("Broker authenticated (data-only use)")

    last_candle_ts = None
    last_auth = time.time()

    while not _stop_event:
        try:
            if not _in_market_hours():
                time.sleep(60)
                continue

            if time.time() - last_auth > 3600:
                broker.authenticate()
                last_auth = time.time()

            candles = broker.get_historical_data("Nifty 50", "ONE_MINUTE", days_back=3, force_refresh=True)
            if not candles:
                time.sleep(POLL_SECONDS)
                continue

            newest_ts = candles[-1]["timestamp"]
            if newest_ts == last_candle_ts:
                time.sleep(POLL_SECONDS)
                continue
            last_candle_ts = newest_ts

            regime = _compute_regime(candles)
            if regime:
                _write_snapshot(regime)
                logger.info(f"REGIME: {regime['market_trend']} | efficiency={regime['efficiency_pct']}% "
                            f"| net_move={regime['net_move_pct']}% | short_move={regime['short_window_net_move_pct']}% "
                            f"| entry_advice={regime['entry_advice']} "
                            f"| session={regime['session_health']} (eff={regime['session_eff_pct']}% "
                            f"net={regime['session_net_pct']}%) | bar={newest_ts}")
        except Exception as exc:
            logger.warning(f"Regime computation error (will retry): {exc}")

        time.sleep(POLL_SECONDS)

    logger.info("Market regime daemon stopped")


if __name__ == "__main__":
    main()
