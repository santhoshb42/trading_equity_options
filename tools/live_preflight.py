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
PILOT = ["pe-itm", "pe-otm"]           # both PE bots: the two that are positive at real fills
                                       # (PE-ITM +Rs156/tr, PE-OTM +Rs55/tr once the post-fix
                                       # book is re-priced at the real bid/ask). CE is negative
                                       # at real fills on both modes, so it stays PAPER.
BOTS = {"ce-itm": "CE_OPTIONS/ITM", "ce-otm": "CE_OPTIONS/OTM",
        "pe-itm": "PUT_OPTIONS/ITM", "pe-otm": "PUT_OPTIONS/OTM"}
# A single 1-lot PE trade costs a MEDIAN of Rs17,050 (191 real contracts, 09-30 & 10-01;
# min Rs6,435, p90 Rs25,938, max Rs29,835). Both PE bots usually take the same signal, so one
# alert consumes TWO lots ~ Rs34,100. Rs50,000 therefore buys only ~1-2 concurrent signal pairs,
# which still produces plenty of fills because positions recycle within minutes - but below that
# most alerts will simply be rejected NO_FUNDS and the sample will be thin.
MIN_FUNDS = 50000.0

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
e_pilot = env_of(PILOT[0]) or {}
_etype = e_pilot.get("OPTIONS_ENTRY_ORDER_TYPE", "MARKET")
check(f"entry order type = {_etype}", _etype == "MARKET",
      "MARKET is DELIBERATE. A LIMIT at ask+1 tick books the entry at the ASK while the position "
      "is then marked at LTP/bid, so the trade opens down by the spread: median 1.90% against an "
      "8% stop is 24% of the way to the stop before it breathes. See "
      "project-instant-hard-sl-history - 06-26..06-30 ran 13-20% of trades stopping inside two "
      "minutes. MARKET is also the honest thing to measure.")
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
    want = "LIVE" if svc in PILOT else "PAPER"
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
# Both PE bots are the pilot, so the SHARED PUT env is the right place - it is read by pe-itm
# and pe-otm and by nothing else. The trap only bites if you want ONE PE bot live: then it must
# go in .env.<mode>, which loads second with override.
shared = (ROOT / "PUT_OPTIONS/tools/.env")
txt = shared.read_text() if shared.exists() else ""
_itm = (ROOT / "PUT_OPTIONS/tools/.env.itm")
_otm = (ROOT / "PUT_OPTIONS/tools/.env.otm")
_stray = [f.name for f in (_itm, _otm)
          if f.exists() and "TRADING_MODE" in f.read_text()]
check("no stray TRADING_MODE in a per-mode env file", not _stray,
      (f"{_stray} also sets it, and .env.<mode> loads AFTER .env with override - it would win "
       f"silently. Remove it, or arm only through the file you intend.") if _stray else "",
      fatal=bool(_stray))
check("CE bots untouched by the PUT env", True,
      "CE reads CE_OPTIONS/tools/.env, so arming the PUT env cannot take a CE bot live",
      fatal=False)
_lots = (env_of(PILOT[0]) or {}).get("OPTIONS_MAX_LOTS_PER_TRADE", "0")
check(f"OPTIONS_MAX_LOTS_PER_TRADE = {_lots}", _lots not in ("0", "", None),
      "pins every entry to N lots whatever the premium. Do NOT use OPTIONS_CAP_PER_TRADE for "
      "this: a rupee cap gives a cheap contract several lots and REJECTS any contract whose one "
      "lot exceeds the budget, biasing the sample toward cheap options - which carry the widest "
      "percentage spreads, i.e. the very thing being measured. Leave the budget at Rs30,000.",
      fatal=False)

print("\n" + ("READY to arm." if ok else "NOT READY - clear the FAILs above first."))
if "--arm" in sys.argv:
    print(f"""
=== commands to arm {PILOT} ONLY (run them yourself; this script never does) ===

  # Both PE bots go live, so set it in the SHARED PUT env - it is read by pe-itm and pe-otm
  # and by nothing else (the CE bots read CE_OPTIONS/tools/.env).
  echo "TRADING_MODE=LIVE"             >> {ROOT}/PUT_OPTIONS/tools/.env
  echo "OPTIONS_MAX_LOTS_PER_TRADE=1"  >> {ROOT}/PUT_OPTIONS/tools/.env
  systemctl restart pe-itm pe-otm

=== then CONFIRM from /proc, because .env lies and .env.itm overrides it ===

  python3 {__file__}

  Expect LIVE on pe-itm and pe-otm, PAPER on ce-itm and ce-otm. To revert:
  sed -i '/^TRADING_MODE=LIVE/d;/^OPTIONS_MAX_LOTS_PER_TRADE=1/d' {ROOT}/PUT_OPTIONS/tools/.env
  systemctl restart pe-itm pe-otm

=== what to read after the first session ===

  D=$(date +%F)
  grep -h BUY_FILL_SLIPPAGE    {ROOT}/PUT_OPTIONS/{{ITM,OTM}}/logs/$D/optbot.log
  grep -h EXIT_FILL_SLIPPAGE   {ROOT}/PUT_OPTIONS/{{ITM,OTM}}/logs/$D/optbot.log
  grep -h PILOT_LOT_CAP        {ROOT}/PUT_OPTIONS/{{ITM,OTM}}/logs/$D/optbot.log
  grep -h NO_FUNDS             {ROOT}/PUT_OPTIONS/{{ITM,OTM}}/logs/$D/optbot.log
  grep -h NO_REAL_BOOK_REJECTED {ROOT}/PUT_OPTIONS/{{ITM,OTM}}/logs/$D/optbot.log

  BUY_FILL_SLIPPAGE is the whole point: real fill vs the price we decided on. That one number
  settles whether the PAPER edge is real. 07-03 benchmark: -Rs182/trade real vs +Rs241 claimed.
""")
sys.exit(0 if ok else 1)
