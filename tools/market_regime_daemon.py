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
    """Authenticate, and do NOT return until the session is actually usable.

    The old version called authenticate() once and ignored its return value.
    authenticate() returns False when AngelOne rate-limits the login -- which it does
    reliably, because all four bots plus this daemon restart within the same ~60s window.
    The daemon then ran unauthenticated and, because re-auth was on a 3600s timer started
    at boot, it stayed broken until exactly boot+1h. Observed 08-25, 08-27 and 08-31:
    210 "Not authenticated for Nifty 50" per session, 09:15 -> 09:50, i.e. the entire
    first 35 minutes of trading had NO regime data.
    """
    from dotenv import load_dotenv
    load_dotenv(CE_DIR / "tools" / ".env")
    os.environ.setdefault("BOT_MODE", "OTM")
    from optcode.angelone_options import AngelOneOptionsBroker

    # Stagger against the bots' own startup logins to avoid the rate limit in the first place.
    _jitter = float(os.getenv("REGIME_AUTH_START_DELAY_SECONDS", "25"))
    if _jitter > 0:
        logger.info(f"Staggering broker login by {_jitter:.0f}s to avoid the bot startup rate limit")
        time.sleep(_jitter)

    broker = AngelOneOptionsBroker()
    _max_wait = float(os.getenv("REGIME_AUTH_MAX_WAIT_SECONDS", "900"))
    _deadline = time.time() + _max_wait
    _attempt = 0
    while not _stop_event:
        _attempt += 1
        try:
            if broker.authenticate(is_retry=_attempt > 1):
                logger.info(f"Broker authenticated (data-only use) | attempt={_attempt}")
                return broker
        except Exception as e:
            logger.warning(f"Broker auth raised on attempt {_attempt}: {e}")
        if time.time() >= _deadline:
            logger.error(
                f"Broker auth still failing after {_max_wait:.0f}s ({_attempt} attempts) -- "
                "continuing; the main loop re-checks the session before every fetch"
            )
            return broker
        _backoff = min(2 ** min(_attempt, 5), 32)
        logger.warning(f"Broker auth attempt {_attempt} FAILED (rate limit?) -- retrying in {_backoff}s")
        time.sleep(_backoff)
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



# ---------------------------------------------------------------------------
# NIFTY's OWN AO + MACD (2026-09-25)
# ---------------------------------------------------------------------------
# Added because the index-to-stock link is REAL and linear: measured over 3,638
# trades, our underlyings move with NIFTY (beta 0.62 on CE, 1.49 on PE; same
# direction 58-59% of the time), and P&L follows NIFTY's move DURING the trade
# monotonically -- CE made +Rs2,411/trade when NIFTY rose >0.10% while the trade
# was open and lost Rs389 when it fell; PE the mirror. What is NOT established is
# whether any entry-time state PREDICTS the next few minutes of NIFTY, which is
# what these series are logged to answer. Computed on 5-minute bars built from the
# 1-minute candles the daemon already fetches - no extra broker call.
_AO_FAST, _AO_SLOW = 5, 34
_MACD_FAST, _MACD_SLOW, _MACD_SIGNAL = 12, 26, 9


def _five_minute_bars(candles):
    """Continuous 5-min bars (NOT reset per day - resetting hides every morning)."""
    bars = []
    for c in candles:
        ts = c["timestamp"]
        slot = (ts[:10], (int(ts[11:13]) * 60 + int(ts[14:16])) // 5)
        if bars and bars[-1]["slot"] == slot:
            b = bars[-1]
            b["high"] = max(b["high"], c["high"])
            b["low"] = min(b["low"], c["low"])
            b["close"] = c["close"]
        else:
            bars.append({"slot": slot, "open": c["open"], "high": c["high"],
                         "low": c["low"], "close": c["close"], "ts": ts})
    return bars


def _ema(values, n):
    k = 2.0 / (n + 1)
    e = None
    out = []
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def _compute_ao_macd(candles):
    """AO (5/34 of median price) and MACD (12/26/9 of close) on 5-min bars."""
    bars = _five_minute_bars(candles)
    if len(bars) < _AO_SLOW + 2:
        return {}
    med = [(b["high"] + b["low"]) / 2 for b in bars]
    close = [b["close"] for b in bars]
    def sma(v, n, i):
        return sum(v[i - n + 1:i + 1]) / n if i >= n - 1 else None
    i = len(bars) - 1
    ao = sma(med, _AO_FAST, i) - sma(med, _AO_SLOW, i)
    ao_prev = (sma(med, _AO_FAST, i - 1) - sma(med, _AO_SLOW, i - 1)) if i >= _AO_SLOW else None
    macd_line = [a - b for a, b in zip(_ema(close, _MACD_FAST), _ema(close, _MACD_SLOW))]
    signal = _ema(macd_line, _MACD_SIGNAL)
    return {
        "nifty_ao": round(ao, 3),
        "nifty_ao_rising": (ao_prev is not None and ao > ao_prev),
        "nifty_ao_delta": round(ao - ao_prev, 3) if ao_prev is not None else None,
        "nifty_macd": round(macd_line[-1], 3),
        "nifty_macd_signal": round(signal[-1], 3),
        "nifty_macd_hist": round(macd_line[-1] - signal[-1], 3),
        "nifty_5m_bars": len(bars),
    }


def _minute_marks(candles):
    """NIFTY's own recent movement, so the log reads without re-deriving it."""
    if not candles:
        return {}
    last = candles[-1]["close"]
    def chg(n):
        if len(candles) <= n or candles[-1 - n]["close"] == 0:
            return None
        return round((last - candles[-1 - n]["close"]) / candles[-1 - n]["close"] * 100, 3)
    return {"nifty_close": last, "nifty_chg_1m": chg(1), "nifty_chg_5m": chg(5),
            "nifty_chg_15m": chg(15), "nifty_chg_30m": chg(30)}


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
        **_minute_marks(candles),
        **_compute_ao_macd(candles),
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

    last_candle_ts = None
    last_auth = time.time()

    while not _stop_event:
        try:
            if not _in_market_hours():
                time.sleep(60)
                continue

            # Re-check the session before EVERY fetch. ensure_authenticated() is cheap when the
            # session is valid and self-heals with backoff when it is not -- this is what stops a
            # rate-limited login from silently costing the first 35 minutes of the session.
            if not broker.ensure_authenticated():
                logger.warning("REGIME: session not usable yet - retrying next poll")
                time.sleep(POLL_SECONDS)
                continue
            if time.time() - last_auth > 3600:
                broker.authenticate(is_retry=True)
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
