#!/usr/bin/env python3
"""
GTT Manager - Daily GTT Order Placer for Long-Term Equity Holdings

Usage:
    python3 gtt_manager.py                      # Review + place GTT orders
    python3 gtt_manager.py --dry-run            # Preview only, no orders placed
    python3 gtt_manager.py --sl 5.0 --tgt 15.0 # Custom SL% and target%
    python3 gtt_manager.py --sl 5.0             # SL only, no target GTT
    python3 gtt_manager.py --status             # Show all active GTT rules
    python3 gtt_manager.py --cancel-all         # Cancel all active GTT rules
    python3 gtt_manager.py --symbol RELIANCE    # Process single symbol only

Reads credentials from: /root/santhosh/trading/equity/.env
"""

import os
import sys
import json
import time
import argparse
import pyotp
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

# ─── Load .env ───────────────────────────────────────────────────────────────
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

# ─── Config ──────────────────────────────────────────────────────────────────
API_KEY      = os.getenv("ANGEL_API_KEY", "")
CLIENT_CODE  = os.getenv("ANGEL_CLIENT_CODE", "")
PASSWORD     = os.getenv("ANGEL_PASSWORD", "")
TOTP_SECRET  = os.getenv("ANGEL_TOTP_SECRET", "")

DEFAULT_SL_PCT     = 5.0    # Default stop-loss %  below avg buy price
DEFAULT_TARGET_PCT = 0.0    # 0 = no target GTT by default
GTT_VALIDITY_DAYS  = 365    # GTT validity (max 365)

LOG_FILE = Path(__file__).parent.parent / "logs" / f"gtt_manager_{datetime.now().strftime('%Y%m%d')}.log"


# ─── Logging ─────────────────────────────────────────────────────────────────
def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {level:7s} | {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ─── AngelOne Login ───────────────────────────────────────────────────────────
def login():
    """Login to AngelOne SmartAPI and return SmartConnect instance."""
    try:
        from SmartApi import SmartConnect
    except ImportError:
        log("SmartApi not installed. Run: pip install smartapi-python", "ERROR")
        sys.exit(1)

    if not all([API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET]):
        log("Missing AngelOne credentials in .env", "ERROR")
        sys.exit(1)

    smart = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()

    log(f"Logging in as {CLIENT_CODE}...")
    data = smart.generateSession(CLIENT_CODE, PASSWORD, totp)

    if data and data.get("status"):
        log(f"✅ Login successful | token={smart.access_token[:10]}...")
        return smart
    else:
        log(f"❌ Login failed: {data}", "ERROR")
        sys.exit(1)


# ─── Holdings ────────────────────────────────────────────────────────────────
def get_holdings(smart) -> List[Dict]:
    """Fetch all delivery holdings with qty > 0."""
    resp = smart.holding()
    if not resp or not resp.get("status"):
        log(f"holdings API error: {resp}", "ERROR")
        return []

    holdings = resp.get("data", []) or []
    active = []
    for h in holdings:
        qty = int(h.get("quantity", 0) or 0)
        if qty > 0:
            active.append(h)

    log(f"Holdings: {len(active)} positions with qty > 0")
    return active


# ─── GTT Rule Helpers ─────────────────────────────────────────────────────────
def get_active_gtt_rules(smart) -> Dict[str, List[Dict]]:
    """
    Fetch all active/triggered GTT rules.
    Returns dict keyed by tradingsymbol → list of rule dicts.
    """
    result: Dict[str, List[Dict]] = {}
    try:
        resp = smart.gttLists(status=["FORALL"], page=1, count=100)
        rules = resp.get("data", []) or [] if resp and resp.get("status") else []
        for rule in rules:
            sym = rule.get("tradingsymbol", "")
            result.setdefault(sym, []).append(rule)
        log(f"Existing GTT rules: {len(rules)} total")
    except Exception as e:
        log(f"gttLists error: {e}", "WARNING")
    return result


def cancel_gtt(smart, rule_id: str, symbol: str) -> bool:
    try:
        smart.gttCancelRule({"id": rule_id, "tradingsymbol": symbol, "exchange": "NSE"})
        log(f"  🗑  Cancelled GTT rule {rule_id} for {symbol}")
        return True
    except Exception as e:
        log(f"  ⚠️  Cancel failed for rule {rule_id}: {e}", "WARNING")
        return False


def create_sl_gtt(smart, holding: Dict, sl_price: float, dry_run: bool) -> Optional[str]:
    """Create a GTT SL-SELL rule for a holding."""
    sym   = holding["tradingsymbol"]
    token = holding.get("symboltoken", "")
    qty   = int(holding.get("quantity", 0))
    ltp   = float(holding.get("ltp", 0) or 0)

    # Limit price = 1% below trigger to ensure fill
    limit_price = round(sl_price * 0.99, 2)

    params = {
        "tradingsymbol": sym,
        "symboltoken":   str(token),
        "exchange":      "NSE",
        "transactiontype": "SELL",
        "producttype":   "DELIVERY",
        "price":         str(limit_price),
        "qty":           str(qty),
        "disclosedqty":  str(qty),
        "triggerprice":  str(round(sl_price, 2)),
        "timeperiod":    str(GTT_VALIDITY_DAYS),
    }

    log(f"  🛡  SL GTT | {sym} | qty={qty} | trigger=₹{sl_price:.2f} | limit=₹{limit_price:.2f} | ltp=₹{ltp:.2f}")

    if dry_run:
        log(f"     [DRY RUN] Would create SL GTT for {sym}")
        return "DRY_RUN"

    try:
        rule_id = smart.gttCreateRule(params)
        log(f"  ✅ Created SL GTT | {sym} | rule_id={rule_id}")
        return str(rule_id)
    except Exception as e:
        log(f"  ❌ Failed to create SL GTT for {sym}: {e}", "ERROR")
        return None


def create_target_gtt(smart, holding: Dict, target_price: float, dry_run: bool) -> Optional[str]:
    """Create a GTT Target-SELL rule for a holding."""
    sym   = holding["tradingsymbol"]
    token = holding.get("symboltoken", "")
    qty   = int(holding.get("quantity", 0))
    ltp   = float(holding.get("ltp", 0) or 0)

    # Limit price = 1% below target trigger (ensures fill on spike)
    limit_price = round(target_price * 0.99, 2)

    params = {
        "tradingsymbol": sym,
        "symboltoken":   str(token),
        "exchange":      "NSE",
        "transactiontype": "SELL",
        "producttype":   "DELIVERY",
        "price":         str(limit_price),
        "qty":           str(qty),
        "disclosedqty":  str(qty),
        "triggerprice":  str(round(target_price, 2)),
        "timeperiod":    str(GTT_VALIDITY_DAYS),
    }

    log(f"  🎯 TARGET GTT | {sym} | qty={qty} | trigger=₹{target_price:.2f} | limit=₹{limit_price:.2f} | ltp=₹{ltp:.2f}")

    if dry_run:
        log(f"     [DRY RUN] Would create target GTT for {sym}")
        return "DRY_RUN"

    try:
        rule_id = smart.gttCreateRule(params)
        log(f"  ✅ Created Target GTT | {sym} | rule_id={rule_id}")
        return str(rule_id)
    except Exception as e:
        log(f"  ❌ Failed to create target GTT for {sym}: {e}", "ERROR")
        return None


# ─── Main Logic ───────────────────────────────────────────────────────────────
def process_holdings(smart, sl_pct: float, tgt_pct: float, dry_run: bool,
                     symbol_filter: Optional[str] = None):
    """
    For each holding:
      - Skip if active GTT SL already exists at >= current SL price
      - Cancel stale SL GTT (if avg price changed / price moved significantly)
      - Create new SL GTT
      - Optionally create target GTT
    """
    holdings    = get_holdings(smart)
    active_gtts = get_active_gtt_rules(smart)

    if not holdings:
        log("No holdings found.", "WARNING")
        return

    created_sl    = 0
    created_tgt   = 0
    skipped       = 0
    already_ok    = 0

    log(f"\n{'─'*60}")
    log(f"Processing {len(holdings)} holdings | SL={sl_pct}% | Target={tgt_pct}%")
    log(f"{'─'*60}")

    for h in holdings:
        sym      = h.get("tradingsymbol", "")
        avg_price = float(h.get("averageprice", 0) or 0)
        qty       = int(h.get("quantity", 0))
        ltp       = float(h.get("ltp", 0) or 0)
        pnl       = float(h.get("pnl", 0) or 0)

        if symbol_filter and sym.upper() != symbol_filter.upper():
            continue

        if avg_price <= 0:
            log(f"  ⚠️  {sym}: avg_price=0, skipping", "WARNING")
            skipped += 1
            continue

        sl_price     = round(avg_price * (1 - sl_pct / 100), 2)
        target_price = round(avg_price * (1 + tgt_pct / 100), 2) if tgt_pct > 0 else None

        pnl_str = f"+₹{pnl:.0f}" if pnl >= 0 else f"-₹{abs(pnl):.0f}"
        log(f"\n📦 {sym} | qty={qty} | avg=₹{avg_price:.2f} | ltp=₹{ltp:.2f} | P&L={pnl_str}")
        log(f"   Calculated SL=₹{sl_price:.2f}  |  Target={'₹'+str(target_price) if target_price else 'N/A'}")

        # ── Check for existing GTT rules on this symbol ──
        existing = active_gtts.get(sym, [])
        existing_sl_rules     = [r for r in existing if float(r.get("triggerprice", 0)) < avg_price]
        existing_target_rules = [r for r in existing if float(r.get("triggerprice", 0)) >= avg_price]

        # ── SL GTT ──
        sl_needed = True
        for rule in existing_sl_rules:
            existing_trigger = float(rule.get("triggerprice", 0))
            diff_pct = abs(existing_trigger - sl_price) / sl_price * 100
            rule_id  = rule.get("id", "?")
            status   = rule.get("status", "?")
            if status not in ("NEW", "ACTIVE"):
                continue
            if diff_pct < 0.5:
                # Close enough — keep it
                log(f"  ✅ SL GTT already active | rule={rule_id} | trigger=₹{existing_trigger:.2f} (within 0.5%)")
                sl_needed = False
                already_ok += 1
            else:
                # Stale — cancel and recreate
                log(f"  🔄 SL GTT stale | rule={rule_id} | old=₹{existing_trigger:.2f} → new=₹{sl_price:.2f}")
                if not dry_run:
                    cancel_gtt(smart, str(rule_id), sym)
                    time.sleep(0.3)

        if sl_needed:
            rule_id = create_sl_gtt(smart, h, sl_price, dry_run)
            if rule_id:
                created_sl += 1
            time.sleep(0.3)

        # ── Target GTT ──
        if target_price:
            tgt_needed = True
            for rule in existing_target_rules:
                existing_trigger = float(rule.get("triggerprice", 0))
                diff_pct = abs(existing_trigger - target_price) / target_price * 100
                rule_id  = rule.get("id", "?")
                status   = rule.get("status", "?")
                if status not in ("NEW", "ACTIVE"):
                    continue
                if diff_pct < 0.5:
                    log(f"  ✅ Target GTT already active | rule={rule_id} | trigger=₹{existing_trigger:.2f} (within 0.5%)")
                    tgt_needed = False
                else:
                    log(f"  🔄 Target GTT stale | rule={rule_id} | old=₹{existing_trigger:.2f} → new=₹{target_price:.2f}")
                    if not dry_run:
                        cancel_gtt(smart, str(rule_id), sym)
                        time.sleep(0.3)

            if tgt_needed:
                rule_id = create_target_gtt(smart, h, target_price, dry_run)
                if rule_id:
                    created_tgt += 1
                time.sleep(0.3)

    log(f"\n{'─'*60}")
    log(f"Summary: SL GTTs created={created_sl} | Target GTTs created={created_tgt} | Already OK={already_ok} | Skipped={skipped}")
    log(f"{'─'*60}")
    if dry_run:
        log("⚠️  DRY RUN — no orders were actually placed")


def show_status(smart):
    """Print all active GTT rules."""
    log("\n=== Active GTT Rules ===")
    try:
        resp = smart.gttLists(status=["FORALL"], page=1, count=100)
        rules = resp.get("data", []) or [] if resp and resp.get("status") else []
        if not rules:
            log("No active GTT rules found.")
            return
        for r in rules:
            sym     = r.get("tradingsymbol", "?")
            trigger = r.get("triggerprice", "?")
            qty     = r.get("qty", "?")
            status  = r.get("status", "?")
            rule_id = r.get("id", "?")
            log(f"  {sym:20s} | trigger=₹{trigger:>10} | qty={qty:>5} | status={status:10s} | id={rule_id}")
    except Exception as e:
        log(f"gttLists error: {e}", "ERROR")


def cancel_all(smart, dry_run: bool):
    """Cancel all active GTT rules."""
    try:
        resp = smart.gttLists(status=["FORALL"], page=1, count=100)
        rules = resp.get("data", []) or [] if resp and resp.get("status") else []
        active = [r for r in rules if r.get("status") in ("NEW", "ACTIVE")]
        log(f"Found {len(active)} active GTT rules to cancel")
        for r in active:
            rule_id = str(r.get("id", ""))
            sym     = r.get("tradingsymbol", "")
            if dry_run:
                log(f"  [DRY RUN] Would cancel rule {rule_id} | {sym}")
            else:
                cancel_gtt(smart, rule_id, sym)
                time.sleep(0.3)
    except Exception as e:
        log(f"Error fetching GTT list: {e}", "ERROR")


# ─── Entry Point ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="GTT Manager — Daily equity stop-loss/target order placement")
    parser.add_argument("--sl",          type=float, default=DEFAULT_SL_PCT,
                        help=f"Stop-loss %% below avg price (default: {DEFAULT_SL_PCT})")
    parser.add_argument("--tgt",         type=float, default=DEFAULT_TARGET_PCT,
                        help=f"Target %% above avg price (0 = no target GTT, default: {DEFAULT_TARGET_PCT})")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Preview only — no orders placed")
    parser.add_argument("--status",      action="store_true",
                        help="Show all active GTT rules and exit")
    parser.add_argument("--cancel-all",  action="store_true",
                        help="Cancel all active GTT rules")
    parser.add_argument("--symbol",      type=str, default=None,
                        help="Process only this symbol (e.g. RELIANCE)")
    args = parser.parse_args()

    log(f"{'='*60}")
    log(f"GTT Manager — {datetime.now().strftime('%d-%b-%Y %H:%M:%S')}")
    log(f"{'='*60}")

    smart = login()

    if args.status:
        show_status(smart)
        return

    if args.cancel_all:
        cancel_all(smart, dry_run=args.dry_run)
        return

    process_holdings(
        smart,
        sl_pct=args.sl,
        tgt_pct=args.tgt,
        dry_run=args.dry_run,
        symbol_filter=args.symbol,
    )


if __name__ == "__main__":
    main()
