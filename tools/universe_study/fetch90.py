"""90-day 1-min fetch, chunked (AngelOne caps ONE_MINUTE at ~30 days/request).
Per-symbol files data/candles90/SYM.json, compact [t,o,h,l,c,v]. Re-runnable (skips done).
Also fetches NIFTY into data/nifty90.json for regime maps.
"""
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("BOT_MODE", "OTM")
os.chdir("/root/santhosh/trading/CE_OPTIONS")
sys.path.insert(0, "/root/santhosh/trading/CE_OPTIONS")
from dotenv import load_dotenv
load_dotenv("tools/.env")
from optcode.angelone_options import AngelOneOptionsBroker

BASE = Path("/root/santhosh/trading/tools/universe_study")
OUT = BASE / "data" / "candles90"
OUT.mkdir(parents=True, exist_ok=True)

b = AngelOneOptionsBroker()
b.authenticate()

today = dt.date.today()
CHUNKS = []
start = today - dt.timedelta(days=90)
cur = start
while cur < today:
    end = min(cur + dt.timedelta(days=29), today)
    CHUNKS.append((cur.strftime("%Y-%m-%d 09:15"), end.strftime("%Y-%m-%d 15:30")))
    cur = end + dt.timedelta(days=1)


def fetch_sym(symbol, exchange="NSE"):
    token = b.get_instrument_token(symbol, exchange=exchange)
    if not token:
        return None
    rows = {}
    for frm, to in CHUNKS:
        got = None
        for a in range(3):
            try:
                resp = b.smart_api.getCandleData({
                    "exchange": exchange, "symboltoken": token,
                    "interval": "ONE_MINUTE", "fromdate": frm, "todate": to})
                if resp and resp.get("status") and resp.get("data"):
                    got = resp["data"]
            except Exception:
                got = None
            if got is not None:
                break
            time.sleep(2 + a * 2)
        for c in (got or []):
            rows[c[0][:16]] = [c[0][:16], float(c[1]), float(c[2]), float(c[3]), float(c[4]),
                               int(c[5]) if len(c) > 5 else 0]
        time.sleep(0.55)
    return [rows[k] for k in sorted(rows)] if rows else None


syms = json.load(open(BASE / "universe.json"))
ok = fail = skip = 0
for i, s in enumerate(syms):
    f = OUT / f"{s}.json"
    if f.exists() and f.stat().st_size > 100000:
        skip += 1
        continue
    data = fetch_sym(s)
    if data and len(data) > 3000:
        json.dump(data, open(f, "w"))
        ok += 1
    else:
        fail += 1
        print(f"FAILED: {s} ({len(data) if data else 0} bars)", flush=True)
    if (i + 1) % 20 == 0:
        print(f"progress {i+1}/{len(syms)} ok={ok} fail={fail} skip={skip}", flush=True)

nf = fetch_sym("Nifty 50")
if nf:
    json.dump(nf, open(BASE / "data" / "nifty90.json", "w"))
print(f"DONE ok={ok} fail={fail} skip={skip} nifty={len(nf or [])}", flush=True)
