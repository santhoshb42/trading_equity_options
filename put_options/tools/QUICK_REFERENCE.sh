#!/usr/bin/env bash
# Quick Reference - Options Bot Paper to Live Trading Transition

cat << 'EOF'

================================================================================
OPTIONS BOT - PAPER TO LIVE TRADING SYSTEM
QUICK REFERENCE CARD
================================================================================

┌─────────────────────────────────────────────────────────────────────────────┐
│ WHAT IS THIS?                                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ Your ML system now tracks trading mode (PAPER/LIVE) for every trade.       │
│ This ensures that learning from paper trading transfers safely to live.     │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ KEY FEATURES                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│ ✅ Mode Tracking      - Every trade tagged PAPER or LIVE                   │
│ ✅ Auto Detection     - Mode auto-detected from config                     │
│ ✅ Readiness Check    - Validator ensures you're ready before switching    │
│ ✅ Paper vs Live      - Compare performance across modes                   │
│ ✅ Reversible         - Can switch back to PAPER anytime                   │
│ ✅ Non-Breaking       - No code changes, no retraining needed              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ WORKFLOW - 30 DAYS TO LIVE TRADING                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Week 1: PAPER TRADING                                                       │
│ ├─ Bot trades in PAPER mode (default)                                       │
│ ├─ Execute 20-30 contracts                                                  │
│ └─ ML learns from all trades                                                │
│                                                                              │
│ Day 14: CHECK READINESS                                                     │
│ ├─ Run: check_paper_live_readiness.py                                       │
│ ├─ System calculates: Readiness Score (0-100)                               │
│ └─ Status: READY / CAUTION / NOT_READY                                      │
│                                                                              │
│ Week 2: SWITCH TO LIVE (if READY)                                           │
│ ├─ Edit optconfig.py: TRADING_MODE = "LIVE"                                │
│ ├─ Restart bot                                                              │
│ └─ All trades tagged: trading_mode = "LIVE"                                 │
│                                                                              │
│ Week 3-4: MONITOR TRANSITION                                                │
│ ├─ Check logs: grep "mode=LIVE" options/logs/options_bot.log               │
│ ├─ Run validator again                                                      │
│ └─ Compare: Paper WR vs Live WR                                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ COMMANDS                                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ CHECK READINESS (Main Command)                                              │
│ ─────────────────────────────────────────────────────────────────────       │
│ $ cd /root/santhosh/trading                                                 │
│ $ python3 options/tools/check_paper_live_readiness.py                       │
│                                                                              │
│ SWITCH TO LIVE MODE                                                         │
│ ─────────────────────────────────────────────────────────────────────       │
│ $ vim options/optcode/optconfig.py                                          │
│ Change: TRADING_MODE = "LIVE"                                               │
│ $ python3 options/main.py  # Restart                                        │
│                                                                              │
│ VIEW PAPER TRADES                                                           │
│ ─────────────────────────────────────────────────────────────────────       │
│ $ grep "mode=PAPER" options/logs/options_bot.log                            │
│                                                                              │
│ VIEW LIVE TRADES                                                            │
│ ─────────────────────────────────────────────────────────────────────       │
│ $ grep "mode=LIVE" options/logs/options_bot.log                             │
│                                                                              │
│ TEST WITH SAMPLE DATA                                                       │
│ ─────────────────────────────────────────────────────────────────────       │
│ $ python3 options/tools/test_transition_validator.py                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ READINESS SCORING                                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Score ≥70  → READY      ✅ Safe to switch to live                           │
│ Score 50-69 → CAUTION   ⚠️  Can trade but monitor                          │
│ Score <50  → NOT_READY  ❌ Need more paper data                             │
│                                                                              │
│ Checks:                                                                      │
│ ✓ Minimum trades (need 10+)                                                │
│ ✓ Win rate (need 50%+)                                                     │
│ ✓ Loss streaks (max 5 consecutive)                                         │
│ ✓ Max loss control (vs average trade)                                      │
│ ✓ Profit factor (wins vs losses)                                           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ WHAT YOU'LL SEE                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ In trade logs:                                                               │
│   SYMBOL_TRACKER: TRADE_RECORDED | NIFTY | mode=PAPER | won=True           │
│   ML_OUTCOME_RECORDED: NIFTY24DEC20450CE | WIN | mode=PAPER | PnL=₹500    │
│                                                                              │
│ When checking readiness:                                                     │
│   Current Status: READY                                                      │
│   Readiness Score: 85/100                                                    │
│   Paper Win Rate: 73.3%                                                      │
│   Total Profit: ₹1,745                                                       │
│   ✅ System is READY for live trading!                                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ FILES TO KNOW ABOUT                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Configuration:                                                               │
│   options/optcode/optconfig.py         - Change TRADING_MODE here           │
│                                                                              │
│ Tools:                                                                       │
│   options/tools/check_paper_live_readiness.py   - Check readiness          │
│   options/tools/test_transition_validator.py    - Test the system          │
│                                                                              │
│ Core Logic:                                                                 │
│   options/optcode/mode_transition_validator.py  - Validation engine        │
│   options/optcode/options_learning_engine.py    - Learning with modes      │
│   options/optcode/opt_ml_integration.py         - ML integration           │
│                                                                              │
│ Documentation:                                                               │
│   options/PAPER_TO_LIVE_TRANSITION.md           - Complete guide           │
│   OPTIONS_BOT_PAPER_LIVE_TRANSITION.md          - Technical details        │
│   OPTIONS_ML_PAPER_LIVE_SUMMARY.md              - Implementation           │
│                                                                              │
│ Data:                                                                        │
│   options/data/trade_history.jsonl              - All trades (with mode)   │
│   options/data/symbol_stats.json                - Per-symbol stats         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ GUARANTEES                                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ ✅ Paper learning is preserved        - Never lost, stays in history       │
│ ✅ Learning transfers to live          - Same ML model used                │
│ ✅ Mode tracking is automatic          - No manual work needed             │
│ ✅ Transition is validated             - Won't switch without readiness    │
│ ✅ Switching is reversible             - Can go back to PAPER anytime      │
│ ✅ No code changes needed              - Just change config flag           │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ IF SOMETHING GOES WRONG                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Live trading performs worse than paper?                                      │
│   → Normal - live trading has slippage and friction                         │
│   → Review first 10-20 live trades                                          │
│   → Check for slippage in entry/exit prices                                 │
│   → Can always switch back to PAPER                                         │
│                                                                              │
│ Not enough paper data?                                                       │
│   → Run validator to see current status                                     │
│   → Trade more contracts in PAPER mode                                      │
│   → Minimum required: 10 trades                                             │
│   → Recommended: 20-30 trades                                               │
│                                                                              │
│ Want to go back to PAPER?                                                    │
│   → Edit optconfig.py: TRADING_MODE = "PAPER"                              │
│   → Restart bot                                                             │
│   → All data preserved, no loss                                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ UNDERSTANDING THE NUMBERS                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Readiness Score Breakdown (100 points):                                     │
│   - Minimum trades (10+):        10-25 points                               │
│   - Win rate (55%+):             20 points                                  │
│   - Max loss control:            15 points                                  │
│   - Loss streak analysis:        15-20 points                               │
│   - Profit factor:               15-20 points                               │
│   - Consistency bonus:           10 points                                  │
│                                                                              │
│ Example with good paper performance:                                         │
│   15 trades, 73% WR, ₹1,745 profit                                          │
│   → Score: 85/100                                                            │
│   → Status: READY                                                            │
│   → Decision: Safe for live                                                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

================================================================================
TL;DR - WHAT YOU NEED TO DO NOW
================================================================================

1. Your bot is already in PAPER mode (default)
2. Trade 20-30 contracts naturally
3. Run: python3 options/tools/check_paper_live_readiness.py
4. If READY (score ≥70): Edit optconfig.py, set TRADING_MODE = "LIVE"
5. Monitor your live trades using same command

That's it! Your ML learns from paper → validated for live → ready for profit! 🚀

================================================================================
Status: ✅ COMPLETE - System is ready to use
Last Updated: 2025-12-14
================================================================================

EOF
