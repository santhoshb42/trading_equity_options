"""
Live P&L Aggregator — trading/live_pnl_aggregator.py

Reads live_data.json and live_data_trades.csv from all 4 bots every INTERVAL
seconds and writes named copies + a merged summary to trading/live_pnl/.

Output layout:
  live_pnl/OTM_CE_live_data.json
  live_pnl/OTM_CE_live_data_trades.csv
  live_pnl/ITM_CE_live_data.json
  live_pnl/ITM_CE_live_data_trades.csv
  live_pnl/OTM_PUT_live_data.json
  live_pnl/OTM_PUT_live_data_trades.csv
  live_pnl/ITM_PUT_live_data.json
  live_pnl/ITM_PUT_live_data_trades.csv
  live_pnl/summary.json          ← merged view across all 4 bots
"""

import json
import logging
import shutil
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

TRADING_DIR = Path(__file__).parent.parent
OUT_DIR = TRADING_DIR / "live_pnl"
INTERVAL = 5  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("live_pnl_agg")

# Bot definitions: (label, data_dir)
BOTS = [
    ("OTM_CE",  TRADING_DIR / "CE_OPTIONS"  / "OTM" / "data"),
    ("ITM_CE",  TRADING_DIR / "CE_OPTIONS"  / "ITM" / "data"),
    ("OTM_PUT", TRADING_DIR / "PUT_OPTIONS" / "OTM" / "data"),
    ("ITM_PUT", TRADING_DIR / "PUT_OPTIONS" / "ITM" / "data"),
]

_stop_event = threading.Event()


def _copy_file(src: Path, dst: Path) -> bool:
    """Copy src → dst atomically. Returns True on success."""
    if not src.exists():
        return False
    try:
        tmp = dst.with_suffix(".tmp")
        shutil.copy2(src, tmp)
        tmp.replace(dst)
        return True
    except Exception as exc:
        logger.warning(f"copy {src} → {dst}: {exc}")
        return False


def _read_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _sum_fields(a: dict, b: dict, keys: list) -> dict:
    """Add numeric fields from dict b into dict a."""
    for k in keys:
        av = a.get(k, 0) or 0
        bv = b.get(k, 0) or 0
        try:
            a[k] = round(float(av) + float(bv), 2)
        except (TypeError, ValueError):
            pass
    return a


ADDITIVE = [
    "budget_used", "trades_today",
    "ongoing_trades", "closed_trades",
    "winning_trades", "losing_trades",
    "total_pnl", "unrealized_pnl", "realized_pnl",
]


def _build_summary(bot_data: dict) -> dict:
    """Merge per-bot live_data into a single summary dict."""
    combined_index = {}
    combined_non_index = {}
    bots_online = 0
    bots_with_positions = 0

    per_bot = {}
    for label, data in bot_data.items():
        if not data:
            per_bot[label] = {"status": "unavailable"}
            continue

        bots_online += 1
        idx = data.get("index_summary", {})
        nidx = data.get("non_index_summary", {})
        ongoing = (idx.get("ongoing_trades") or 0) + (nidx.get("ongoing_trades") or 0)
        if ongoing > 0:
            bots_with_positions += 1

        _sum_fields(combined_index,    idx,  ADDITIVE)
        _sum_fields(combined_non_index, nidx, ADDITIVE)

        per_bot[label] = {
            "status": "online",
            "market_status": data.get("market_status"),
            "trading_mode": data.get("trading_mode"),
            "last_updated": data.get("timestamp"),
            "index": {k: idx.get(k) for k in ADDITIVE + ["trade_limit", "win_rate_percent"]},
            "non_index": {k: nidx.get(k) for k in ADDITIVE + ["trade_limit", "win_rate_percent"]},
        }

    total_ongoing  = (combined_index.get("ongoing_trades")  or 0) + (combined_non_index.get("ongoing_trades")  or 0)
    total_realized = (combined_index.get("realized_pnl")    or 0) + (combined_non_index.get("realized_pnl")    or 0)
    total_unrealzd = (combined_index.get("unrealized_pnl")  or 0) + (combined_non_index.get("unrealized_pnl")  or 0)
    total_trades   = (combined_index.get("trades_today")    or 0) + (combined_non_index.get("trades_today")    or 0)

    return {
        "aggregated_at": datetime.now().isoformat(),
        "bots_online": bots_online,
        "bots_with_positions": bots_with_positions,
        "combined": {
            "total_ongoing_positions": total_ongoing,
            "total_trades_today": total_trades,
            "total_unrealized_pnl": round(total_unrealzd, 2),
            "total_realized_pnl": round(total_realized, 2),
            "total_pnl": round(total_realized + total_unrealzd, 2),
        },
        "index_combined": combined_index,
        "non_index_combined": combined_non_index,
        "bots": per_bot,
    }


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def _run_once() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bot_data = {}

    for label, data_dir in BOTS:
        # Copy live_data.json
        src_json = data_dir / "live_data.json"
        dst_json = OUT_DIR / f"{label}_live_data.json"
        _copy_file(src_json, dst_json)
        bot_data[label] = _read_json(src_json)

        # Copy live_data_trades.csv
        src_csv = data_dir / "live_data_trades.csv"
        dst_csv = OUT_DIR / f"{label}_live_data_trades.csv"
        _copy_file(src_csv, dst_csv)

    summary = _build_summary(bot_data)
    _write_json(OUT_DIR / "summary.json", summary)

    ongoing = summary["combined"]["total_ongoing_positions"]
    pnl     = summary["combined"]["total_pnl"]
    online  = summary["bots_online"]
    logger.debug(f"Aggregated | bots={online}/4 | ongoing={ongoing} | total_pnl=₹{pnl:.2f}")


def _loop() -> None:
    logger.info(f"Live P&L Aggregator started | interval={INTERVAL}s | out={OUT_DIR}")
    while not _stop_event.is_set():
        try:
            _run_once()
        except Exception as exc:
            logger.error(f"Aggregation error: {exc}")
        _stop_event.wait(INTERVAL)
    logger.info("Live P&L Aggregator stopped")


def start_aggregator(interval: int = INTERVAL) -> threading.Thread:
    """Start aggregator as a background daemon thread."""
    global INTERVAL
    INTERVAL = interval
    t = threading.Thread(target=_loop, name="live-pnl-agg", daemon=True)
    t.start()
    return t


def stop_aggregator() -> None:
    _stop_event.set()


if __name__ == "__main__":
    def _handle_signal(sig, _frame):
        logger.info(f"Signal {sig} received, stopping...")
        stop_aggregator()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    _loop()
