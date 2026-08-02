"""30-day 1-min candle fetch for the full F&O universe, stored per-symbol
(data/candles30/SYMBOL.json) so analysis can stream one symbol at a time —
the 2GB VPS runs 4 live bots; never load the whole universe into RAM.
AngelOne ONE_MINUTE history allows max ~30 days back. One call per symbol.
Re-runnable: skips symbols already fetched (delete a file to refetch it)."""
import json, os, sys, time
from pathlib import Path

os.environ.setdefault("BOT_MODE", "OTM")
os.chdir("/root/santhosh/trading/CE_OPTIONS")
sys.path.insert(0, "/root/santhosh/trading/CE_OPTIONS")
from dotenv import load_dotenv
load_dotenv("tools/.env")
from optcode.angelone_options import AngelOneOptionsBroker

BASE = Path("/root/santhosh/trading/tools/universe_study")
OUTDIR = BASE / "data" / "candles30"
OUTDIR.mkdir(parents=True, exist_ok=True)
syms = json.load(open(BASE / "universe.json"))

b = AngelOneOptionsBroker()
b.authenticate()

REFRESH = os.getenv("UNIVERSE_REFRESH", "0") == "1"  # nightly job sets 1 to re-pull the window

ok = fail = skip = 0
for i, s in enumerate(syms):
    f = OUTDIR / f"{s}.json"
    if not REFRESH and f.exists() and f.stat().st_size > 1000:
        skip += 1
        continue
    got = None
    for attempt in range(3):
        try:
            got = b.get_historical_data(s, "ONE_MINUTE", days_back=30)
        except Exception:
            got = None
        if got:
            break
        time.sleep(2.0 + attempt * 2)
    if got:
        compact = [[c["timestamp"][:16], c["open"], c["high"], c["low"], c["close"], c["volume"]]
                   for c in got]
        json.dump(compact, open(f, "w"))
        ok += 1
    else:
        fail += 1
        print(f"FAILED: {s}", flush=True)
    if (i + 1) % 25 == 0:
        print(f"progress {i+1}/{len(syms)} ok={ok} fail={fail} skip={skip}", flush=True)
    time.sleep(0.45)

print(f"DONE ok={ok} fail={fail} skip={skip} total={len(syms)}", flush=True)
