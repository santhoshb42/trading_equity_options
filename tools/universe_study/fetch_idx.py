import json, os, sys, time
from pathlib import Path

os.environ.setdefault("BOT_MODE", "OTM")
os.chdir("/root/santhosh/trading/CE_OPTIONS")
sys.path.insert(0, "/root/santhosh/trading/CE_OPTIONS")
from dotenv import load_dotenv
load_dotenv("tools/.env")
from optcode.angelone_options import AngelOneOptionsBroker

OUT = Path("/tmp/claude-0/-root-santhosh-trading/98d4451b-b7bc-4a03-8c57-47e6e8a5a7c0/scratchpad/backtest")
store = json.load(open(OUT / "candles.json"))
ALIASES = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank"}

b = AngelOneOptionsBroker()
b.authenticate()
for sym, alias in ALIASES.items():
    if store.get(sym):
        continue
    got = None
    for attempt in range(3):
        try:
            got = b.get_historical_data(alias, "ONE_MINUTE", days_back=4)
        except Exception:
            got = None
        if got:
            break
        time.sleep(2)
    if got:
        store[sym] = [{"t": c["timestamp"], "o": c["open"], "h": c["high"], "l": c["low"],
                       "c": c["close"], "v": c["volume"]} for c in got]
        print(f"patched {sym} via '{alias}': {len(got)} bars")
    else:
        print(f"still failed: {sym}")
json.dump(store, open(OUT / "candles.json", "w"))
empty = [s for s, v in store.items() if not v]
print(f"empty symbols remaining: {empty}")
