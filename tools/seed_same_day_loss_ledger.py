#!/usr/bin/env python3
"""
Seed today's same-day loss ledger from the four bots' option_pnl_history.json.

Only needed once, when the re-entry gate is first deployed mid-session: the ledger
is a plain file, so from then on it accumulates by itself and survives restarts.

Usage:  python3 tools/seed_same_day_loss_ledger.py [YYYY-MM-DD] [--write]
Without --write it only reports what it would add.
"""
import json, sys, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [("CE_OPTIONS", "ITM"), ("CE_OPTIONS", "OTM"), ("PUT_OPTIONS", "ITM"), ("PUT_OPTIONS", "OTM")]

args = [a for a in sys.argv[1:] if not a.startswith("--")]
day = args[0] if args else datetime.datetime.now().date().isoformat()
write = "--write" in sys.argv

ledger = ROOT / "live_pnl" / f"same_day_losses_{day}.jsonl"
existing = set()
if ledger.exists():
    for line in ledger.read_text().splitlines():
        try:
            r = json.loads(line)
            existing.add((r.get("underlying"), r.get("ts")))
        except Exception:
            pass

rows = []
for family, mode in BOTS:
    hist = ROOT / family / mode / "data" / "option_pnl_history.json"
    if not hist.exists():
        print(f"  skip (missing): {hist}")
        continue
    for r in json.load(open(hist)):
        if (r.get("entry_time") or "")[:10] != day:
            continue
        pnl = r.get("pnl")
        if pnl is None or float(pnl) >= 0:
            continue
        u = str(r.get("underlying") or "").strip().upper()
        if not u:
            continue
        ts = r.get("closed_at") or r.get("entry_time")
        if (u, ts) in existing:
            continue
        rows.append({
            "ts": ts,
            "underlying": u,
            "side": str(r.get("contract_type") or "").upper(),
            "bot": f"{family}:{mode}",
            "pnl": round(float(pnl), 2),
            "exit_reason": str(r.get("exit_reason") or "")[:40],
            "seeded": True,
        })

rows.sort(key=lambda r: r["ts"] or "")
syms = sorted({r["underlying"] for r in rows})
print(f"\nday={day}  losing closes found: {len(rows)}  distinct symbols: {len(syms)}")
print(f"  ledger: {ledger}  (exists={ledger.exists()}, already has {len(existing)} rows)")
print(f"  symbols that would be blocked: {', '.join(syms) if syms else '(none)'}")
if write:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"\n  WROTE {len(rows)} rows -> {ledger}")
else:
    print("\n  dry run — pass --write to apply")
