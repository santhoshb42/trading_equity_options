#!/usr/bin/env python3
"""Pre-flight for switching ONE bot to LIVE. Read-only: it changes nothing.

WHY THIS EXISTS: the last real-money session (2026-07-03) ran for 3 1/4 hours and lost
~Rs14,500 on a Rs1.2L wallet, two days after PAPER had reported +Rs51,109 and +Rs47,730.
The difference is that PAPER models its own fills while LIVE books the broker's. That makes a
LIVE pilot a MEASUREMENT, not a deployment: the number we want is the real fill cost per trade.
Benchmark to beat: 07-03 came in at -Rs182/trade real against the +Rs241/trade PAPER claimed.

Usage:  python3 tools/live_preflight.py            # check
        python3 tools/live_preflight.py --arm      # print the exact commands (does NOT run them)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = "pe-itm"                       # only bot positive at real fills, 6/7 post-fix days up
BOTS = {"ce-itm": "CE_OPTIONS/ITM", "ce-otm": "CE_OPTIONS/OTM",
        "pe-itm": "PUT_OPTIONS/ITM", "pe-otm": "PUT_OPTIONS/OTM"}
MIN_FUNDS = 25000.0                    # a few 1-lot PE trades plus headroom

ok = True
def check(label, passed, detail="", fatal=True):
    global ok
    mark = "PASS" if passed else ("FAIL" if fatal else "WARN")
    if not passed and fatal:
        ok = False
    print(f"  [{mark}] {label}" + (f"\n         {detail}" if detail else ""))

def env_of(svc):
    """Runtime truth from /proc, never the .env file - .env is overridden by .env.<mode>."""
    try:
        pid = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", svc],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if not pid or pid == "0":
            return None
        raw = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
        return dict(kv.split("=", 1) for kv in raw.split("\0") if "=" in kv)
    except Exception:
        return None

print(__doc__.split("Usage:")[0].strip())
print("\n=== 1. code that must be running ===")
api = (ROOT / "PUT_OPTIONS/optcode/optapi.py").read_text()
mon = (ROOT / "PUT_OPTIONS/optcode/optmonitor.py").read_text()
check("no-real-book gate present (c9cc198)", "NO_REAL_BOOK_REJECTED" in api,
      "blocks a MARKET order into a fabricated ltp*0.98/1.02 book - the MAZDOCK -39% case")
check("HARD_SL ticks keyed on the price sample (70eb053)", "hard_sl_last_tick_sample" in mon,
      "a wall-clock proxy let one print fire a stop; 4 of 36 stops did so over 09-29..10-01")
check("LIVE exit slippage is captured", "EXIT_FILL_SLIPPAGE" in mon,
      "without this the pilot measures NOTHING on exits: exit_premium is overwritten by the "
      "broker fill and intended_exit was then read from that same value, so slippage logged as "
      "exactly 0.00 - and a broker-fired SL skipped the record entirely")

print("\n=== 1b. is slippage CONTROLLED or just accepted? ===")
_et = os.environ.get("OPTIONS_ENTRY_ORDER_TYPE")
e_pilot = env_of(PILOT) or {}
_etype = e_pilot.get("OPTIONS_ENTRY_ORDER_TYPE", "MARKET")
check(f"entry order type = {_etype}", _etype == "LIMIT",
      "MARKET means we accept whatever the book gives - there is no price to tune. LIMIT at "
      "ask+1 tick BOUNDS the entry cost; an unfilled order is cancelled after 30s and the entry "
      "is simply skipped (no position, no orphan). Set OPTIONS_ENTRY_ORDER_TYPE=LIMIT",
      fatal=False)
check("exit order type", False,
      "exit is hard-coded MARKET and the stop is STOPLOSS_MARKET - both uncontrolled by design. "
      "Leave it: not getting out is worse than slipping. At 1 lot the rupee damage is small and "
      "EXIT_FILL_SLIPPAGE now measures it. Revisit only once there is real data.",
      fatal=False)

print("\n=== 2. live positions and mode ===")
for svc, d in BOTS.items():
    e = env_of(svc)
    if e is None:
        check(f"{svc} running", False, "service is not up")
        continue
    mode = e.get("TRADING_MODE", "?")
    want = "LIVE" if svc == PILOT else "PAPER"
    check(f"{svc} TRADING_MODE={mode}", True, f"(expected {want} once armed)", fatal=False)
    try:
        n = len(json.loads((ROOT / d / "data/option_positions.json").read_text()).get("positions") or [])
    except Exception:
        n = -1
    check(f"{svc} open positions = {n}", n == 0,
          "NEVER flip modes with a position open: the bot would place real SELL/SL orders "
          "against a position the broker does not have")

print("\n=== 3. broker account ===")
try:
    sys.path[:0] = [str(ROOT / "PUT_OPTIONS"), str(ROOT), str(ROOT / "tools")]
    os.environ.setdefault("BOT_MODE", "ITM")
    from optcode.angelone_options import get_options_broker
    f = get_options_broker().get_funds_snapshot() or {}
    cash = float(f.get("available_cash") or f.get("net") or 0)
    check(f"available cash Rs{cash:,.2f}", cash >= MIN_FUNDS,
          f"need at least Rs{MIN_FUNDS:,.0f} - a rejected order teaches us nothing about fills")
except Exception as e:
    check("funds readable", False, f"{e}")

print("\n=== 4. pilot sizing ===")
itm = (ROOT / "PUT_OPTIONS/tools/.env.itm")
txt = itm.read_text() if itm.exists() else ""
check("TRADING_MODE set in .env.itm, not the shared .env",
      "TRADING_MODE" in txt,
      "the shared .env is read by BOTH PE bots - setting it there arms pe-otm as well",
      fatal=False)
check("OPTIONS_CAP_PER_TRADE pinned for the pilot", "OPTIONS_CAP_PER_TRADE" in txt,
      "budget is the only size lever; there is no max-lots setting. Rs30,000 buys 3-4 PE lots",
      fatal=False)

print("\n" + ("READY to arm." if ok else "NOT READY - clear the FAILs above first."))
if "--arm" in sys.argv:
    print(f"""
=== commands to arm {PILOT} ONLY (run them yourself; this script never does) ===

  echo "TRADING_MODE=LIVE"            >> {ROOT}/PUT_OPTIONS/tools/.env.itm
  echo "OPTIONS_CAP_PER_TRADE=10000"  >> {ROOT}/PUT_OPTIONS/tools/.env.itm
  systemctl restart {PILOT}

=== then CONFIRM from /proc, because .env lies and .env.itm overrides it ===

  python3 {__file__}

  Expect exactly one LIVE: {PILOT}. If any other bot reads LIVE, stop and revert:
  sed -i '/^TRADING_MODE=LIVE/d' {ROOT}/PUT_OPTIONS/tools/.env.itm && systemctl restart {PILOT}

=== what to read after the first session ===

  grep BUY_FILL_SLIPPAGE   {ROOT}/PUT_OPTIONS/ITM/logs/$(date +%%F)/optbot.log
  grep BUY_CONFIRMATION    {ROOT}/PUT_OPTIONS/ITM/logs/$(date +%%F)/optbot.log
  grep NO_REAL_BOOK_REJECTED {ROOT}/PUT_OPTIONS/ITM/logs/$(date +%%F)/optbot.log

  BUY_FILL_SLIPPAGE is the whole point: real fill vs the price we decided on. That one number
  settles whether the PAPER edge is real. 07-03 benchmark: -Rs182/trade real vs +Rs241 claimed.
""")
sys.exit(0 if ok else 1)
