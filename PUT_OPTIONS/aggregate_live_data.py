"""
Aggregates OTM and ITM live data into a single PUT_OPTIONS-level view.
Reads from OTM/data/ and ITM/data/, writes PUT_OPTIONS/live_data.json
and PUT_OPTIONS/live_data_trades.csv with mode column added.

Designed to run as a background daemon thread inside each bot process.
Only one instance (first writer wins each cycle) actually merges — both
call it safely via a file-level atomic write so no data race.
"""

import json
import threading
import time
import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
OTM_DATA = BASE_DIR / "OTM" / "data"
ITM_DATA = BASE_DIR / "ITM" / "data"
OUT_JSON  = BASE_DIR / "live_data.json"
OUT_CSV   = BASE_DIR / "live_data_trades.csv"

_lock = threading.Lock()
_stop = threading.Event()


def _read_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _merge_live_json() -> dict:
    otm = _read_json(OTM_DATA / "live_data.json")
    itm = _read_json(ITM_DATA / "live_data.json")

    if not otm and not itm:
        return {}

    def _summary(d: dict) -> dict:
        return d.get("index_summary", {})

    def _add(a: dict, b: dict, key: str, default=0):
        return a.get(key, default) + b.get(key, default)

    otm_s = _summary(otm)
    itm_s = _summary(itm)

    merged_summary = {
        "budget_used":           _add(otm_s, itm_s, "budget_used"),
        "trades_today":          _add(otm_s, itm_s, "trades_today"),
        "trade_limit":           _add(otm_s, itm_s, "trade_limit"),
        "trade_slots_remaining": _add(otm_s, itm_s, "trade_slots_remaining"),
        "ongoing_trades":        _add(otm_s, itm_s, "ongoing_trades"),
        "closed_trades":         _add(otm_s, itm_s, "closed_trades"),
        "winning_trades":        _add(otm_s, itm_s, "winning_trades"),
        "losing_trades":         _add(otm_s, itm_s, "losing_trades"),
        "total_pnl":             _add(otm_s, itm_s, "total_pnl"),
        "unrealized_pnl":        _add(otm_s, itm_s, "unrealized_pnl"),
        "realized_pnl":          _add(otm_s, itm_s, "realized_pnl"),
    }
    closed = merged_summary["closed_trades"]
    wins   = merged_summary["winning_trades"]
    merged_summary["win_rate_percent"] = round(wins / closed * 100, 1) if closed else 0.0
    total_invested = _add(otm_s, itm_s, "budget_used")
    merged_summary["total_pnl_percent"] = round(
        merged_summary["total_pnl"] / total_invested * 100, 2
    ) if total_invested else 0.0

    return {
        "timestamp": datetime.now().isoformat(),
        "generated_by": "aggregate_live_data",
        "trading_mode": otm.get("trading_mode") or itm.get("trading_mode", "PAPER"),
        "market_status": otm.get("market_status") or itm.get("market_status", "UNKNOWN"),
        "otm_updated_at": otm.get("timestamp"),
        "itm_updated_at": itm.get("timestamp"),
        "combined_summary": merged_summary,
        "otm": {
            "index_summary": otm_s,
            "non_index_summary": otm.get("non_index_summary", {}),
        },
        "itm": {
            "index_summary": itm_s,
            "non_index_summary": itm.get("non_index_summary", {}),
        },
    }


def _read_csv_trades(path: Path, mode_label: str) -> tuple:
    """Return (ongoing_rows, closed_rows) with mode prepended."""
    ongoing, closed = [], []
    if not path.exists():
        return ongoing, closed
    try:
        with open(path) as f:
            lines = f.readlines()
        section = None
        for line in lines:
            s = line.rstrip("\n")
            if "CLOSED TRADES" in s:
                section = "closed"
                continue
            if "ONGOING TRADES" in s:
                section = "ongoing"
                continue
            if s.startswith("---") or s.startswith("Sts |") or not s.strip():
                continue
            if section == "closed":
                closed.append(f"{mode_label} | {s}")
            elif section == "ongoing":
                ongoing.append(f"{mode_label} | {s}")
    except Exception:
        pass
    return ongoing, closed


def _merge_csv() -> str:
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    otm_on, otm_cl = _read_csv_trades(OTM_DATA / "live_data_trades.csv", "OTM")
    itm_on, itm_cl = _read_csv_trades(ITM_DATA / "live_data_trades.csv", "ITM")

    header = (
        f"PUT_OPTIONS Aggregated | Last Updated: {now}\n"
        f"OTM data: {OTM_DATA / 'live_data_trades.csv'}\n"
        f"ITM data: {ITM_DATA / 'live_data_trades.csv'}\n\n"
    )

    col_header = "Mod | Sts | Underlying | Symbol                   | AlrtPx    | Time  | Entry    | Exit/Curr | High     | Low      | Qty    | PnL      | PnL%  | Dur   | Reason"
    divider    = "-" * len(col_header)

    closed_section = "=== CLOSED TRADES (Today) ===\n" + col_header + "\n" + divider + "\n"
    if otm_cl or itm_cl:
        closed_section += "\n".join(otm_cl + itm_cl) + "\n"

    ongoing_section = "\n=== ONGOING TRADES (Live) ===\n" + col_header + "\n" + divider + "\n"
    if otm_on or itm_on:
        ongoing_section += "\n".join(otm_on + itm_on) + "\n"

    return header + closed_section + ongoing_section


def aggregate_once():
    """Run one aggregation cycle. Thread-safe."""
    with _lock:
        merged = _merge_live_json()
        if merged:
            tmp = OUT_JSON.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(merged, f, indent=2, default=str)
            os.replace(tmp, OUT_JSON)

        csv_content = _merge_csv()
        tmp_csv = OUT_CSV.with_suffix(".tmp")
        with open(tmp_csv, "w") as f:
            f.write(csv_content)
        os.replace(tmp_csv, OUT_CSV)


def _loop(interval: int = 10):
    while not _stop.wait(interval):
        try:
            aggregate_once()
        except Exception:
            pass


def start_aggregator(interval: int = 10) -> threading.Thread:
    """Start background aggregator thread. Call once from main.py."""
    t = threading.Thread(target=_loop, args=(interval,), daemon=True, name="pe-aggregator")
    t.start()
    return t


def stop_aggregator():
    _stop.set()
