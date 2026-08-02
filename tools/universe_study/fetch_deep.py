import json, os, sys, time
from pathlib import Path

os.environ.setdefault("BOT_MODE", "OTM")
os.chdir("/root/santhosh/trading/CE_OPTIONS")
sys.path.insert(0, "/root/santhosh/trading/CE_OPTIONS")
from dotenv import load_dotenv
load_dotenv("tools/.env")
from optcode.angelone_options import AngelOneOptionsBroker

OUT = Path("/tmp/claude-0/-root-santhosh-trading/98d4451b-b7bc-4a03-8c57-47e6e8a5a7c0/scratchpad/backtest")
syms = json.load(open(OUT / "universe.json"))
store_path = OUT / "candles10.json"
store = json.load(open(store_path)) if store_path.exists() else {}

b = AngelOneOptionsBroker()
b.authenticate()

ok = fail = 0
for i, s in enumerate(syms):
    if s in store and store[s]:
        ok += 1
        continue
    got = None
    for attempt in range(3):
        try:
            got = b.get_historical_data(s, "ONE_MINUTE", days_back=10)
        except Exception:
            got = None
        if got:
            break
        time.sleep(2.0 + attempt * 2)
    if got:
        # keep only needed fields, trim to the 3 analysis days + prior day for indicator warmup
        store[s] = [{"t": c["timestamp"], "o": c["open"], "h": c["high"], "l": c["low"],
                     "c": c["close"], "v": c["volume"]} for c in got]
        ok += 1
    else:
        store[s] = []
        fail += 1
        print(f"FAILED: {s}", flush=True)
    if (i + 1) % 20 == 0:
        json.dump(store, open(store_path, "w"))
        print(f"progress {i+1}/{len(syms)} ok={ok} fail={fail}", flush=True)
    time.sleep(0.45)  # historical endpoint 3/s cap — stay well under

json.dump(store, open(store_path, "w"))
print(f"DONE ok={ok} fail={fail} total={len(syms)}", flush=True)
