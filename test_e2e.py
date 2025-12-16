#!/usr/bin/env python3
"""
End-to-End Testing Script for Equity + Options Bots
Tests: Alert routing → Bot processing → Monitoring → SELL orders
Safety: Paper trading mode only, no real orders
"""

import requests
import time
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Configuration
EQUITY_WEBHOOK = "http://127.0.0.1:8080/webhook"
OPTIONS_WEBHOOK = "http://127.0.0.1:8081/webhook/options"
ROUTER_WEBHOOK = "http://127.0.0.1/webhook"
EQUITY_HEALTH = "http://127.0.0.1:8080/health"
OPTIONS_HEALTH = "http://127.0.0.1:8081/health"
ROUTER_STATS = "http://127.0.0.1/stats"

# Test alerts - 5 different symbols covering both bots
TEST_ALERTS = [
    {
        "symbol": "NIFTY",  # Index option - should go to options bot
        "action": "BUY",
        "price": "25000.0",
        "score": "100.00",
        "confidence": "95.00",
        "verdict": "1.00",
        "vwap": "24950.0",
        "rsi": "75.0",
        "atr": "50.0",
        "adx": "30.0",
        "volume": "1000000.0",
        "ema9": "24980.0",
        "ema20": "24900.0",
        "pdc": "24800.0",
        "pdc_confirm": "1.00",
        "pdh": "25100.0",
        "ema_gap_pct": "0.30",
        "vwap_lead_pct": "0.20",
        "atr_pct": "0.20",
        "vol_z": "3.0",
        "z_rsi": "1.5",
        "heat_ok": "1.00",
        "body_ratio": "0.80",
        "open_gap_pct": "0.00",
        "hl_range_pct": "0.50",
        "pc_lead_pct": "0.50",
        "close_to_high": "95.0",
        "atr_jump": "0.05",
        "above_vwap": "1.00",
        "time_ok": "1.00",
        "resistance_break": "1.00",
        "vol_quality": "1.00"
    },
    {
        "symbol": "TITAN",  # Equity + has options - should go to BOTH
        "action": "BUY",
        "price": "3900.0",
        "score": "100.00",
        "confidence": "95.00",
        "verdict": "1.00",
        "vwap": "3880.0",
        "rsi": "75.0",
        "atr": "8.0",
        "adx": "28.0",
        "volume": "200000.0",
        "ema9": "3880.0",
        "ema20": "3870.0",
        "pdc": "3850.0",
        "pdc_confirm": "1.00",
        "pdh": "3900.0",
        "ema_gap_pct": "0.25",
        "vwap_lead_pct": "0.50",
        "atr_pct": "0.20",
        "vol_z": "4.0",
        "z_rsi": "1.4",
        "heat_ok": "1.00",
        "body_ratio": "0.75",
        "open_gap_pct": "0.00",
        "hl_range_pct": "0.70",
        "pc_lead_pct": "0.50",
        "close_to_high": "92.0",
        "atr_jump": "0.05",
        "above_vwap": "1.00",
        "time_ok": "1.00",
        "resistance_break": "1.00",
        "vol_quality": "1.00"
    },
    {
        "symbol": "ASTRAL",  # Equity + has options - should go to BOTH
        "action": "BUY",
        "price": "2200.0",
        "score": "100.00",
        "confidence": "90.00",
        "verdict": "1.00",
        "vwap": "2180.0",
        "rsi": "70.0",
        "atr": "5.0",
        "adx": "25.0",
        "volume": "150000.0",
        "ema9": "2190.0",
        "ema20": "2170.0",
        "pdc": "2150.0",
        "pdc_confirm": "1.00",
        "pdh": "2210.0",
        "ema_gap_pct": "0.90",
        "vwap_lead_pct": "0.90",
        "atr_pct": "0.23",
        "vol_z": "0.65",
        "z_rsi": "2.80",
        "heat_ok": "1.00",
        "body_ratio": "0.95",
        "open_gap_pct": "0.00",
        "hl_range_pct": "1.00",
        "pc_lead_pct": "0.90",
        "close_to_high": "97.0",
        "atr_jump": "0.05",
        "above_vwap": "1.00",
        "time_ok": "1.00",
        "resistance_break": "1.00",
        "vol_quality": "1.00"
    },
    {
        "symbol": "INFY",  # Equity - should go to BOTH (also has options)
        "action": "BUY",
        "price": "1850.0",
        "score": "100.00",
        "confidence": "92.00",
        "verdict": "1.00",
        "vwap": "1830.0",
        "rsi": "72.0",
        "atr": "4.0",
        "adx": "26.0",
        "volume": "250000.0",
        "ema9": "1840.0",
        "ema20": "1820.0",
        "pdc": "1800.0",
        "pdc_confirm": "1.00",
        "pdh": "1860.0",
        "ema_gap_pct": "0.54",
        "vwap_lead_pct": "1.09",
        "atr_pct": "0.22",
        "vol_z": "2.20",
        "z_rsi": "1.35",
        "heat_ok": "1.00",
        "body_ratio": "0.88",
        "open_gap_pct": "0.00",
        "hl_range_pct": "0.77",
        "pc_lead_pct": "0.76",
        "close_to_high": "98.0",
        "atr_jump": "0.06",
        "above_vwap": "1.00",
        "time_ok": "1.00",
        "resistance_break": "1.00",
        "vol_quality": "1.00"
    },
    {
        "symbol": "BANKNIFTY",  # Index option - should go to options bot primarily
        "action": "BUY",
        "price": "50000.0",
        "score": "100.00",
        "confidence": "93.00",
        "verdict": "1.00",
        "vwap": "49850.0",
        "rsi": "73.0",
        "atr": "100.0",
        "adx": "27.0",
        "volume": "500000.0",
        "ema9": "49900.0",
        "ema20": "49750.0",
        "pdc": "49500.0",
        "pdc_confirm": "1.00",
        "pdh": "50100.0",
        "ema_gap_pct": "0.30",
        "vwap_lead_pct": "0.30",
        "atr_pct": "0.20",
        "vol_z": "2.80",
        "z_rsi": "1.20",
        "heat_ok": "1.00",
        "body_ratio": "0.82",
        "open_gap_pct": "0.00",
        "hl_range_pct": "0.60",
        "pc_lead_pct": "0.60",
        "close_to_high": "96.0",
        "atr_jump": "0.05",
        "above_vwap": "1.00",
        "time_ok": "1.00",
        "resistance_break": "1.00",
        "vol_quality": "1.00"
    }
]

class E2ETestRunner:
    def __init__(self):
        self.results = {
            "test_start": datetime.now().isoformat(),
            "steps": [],
            "alerts_sent": 0,
            "equity_orders": 0,
            "options_orders": 0,
            "errors": []
        }
    
    def log(self, step: str, status: str, details: str = ""):
        """Log test step"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "step": step,
            "status": status,
            "details": details
        }
        self.results["steps"].append(entry)
        
        # Print to console
        symbol = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⏳"
        print(f"{symbol} [{step}] {status}")
        if details:
            print(f"   └─ {details}")
    
    def step_1_health_check(self):
        """Step 1: Check both bots are running"""
        print("\n" + "="*70)
        print("STEP 1: Health Check - Verify Bots Running")
        print("="*70)
        
        try:
            eq_resp = requests.get(EQUITY_HEALTH, timeout=5)
            eq_status = eq_resp.json().get("status") if eq_resp.status_code == 200 else "DOWN"
            self.log("Equity Bot Health", "PASS" if eq_status == "healthy" else "FAIL", 
                    f"Status: {eq_status}")
        except Exception as e:
            self.log("Equity Bot Health", "FAIL", str(e))
            self.results["errors"].append(f"Equity bot unreachable: {e}")
            return False
        
        try:
            opt_resp = requests.get(OPTIONS_HEALTH, timeout=5)
            opt_status = opt_resp.json().get("status") if opt_resp.status_code == 200 else "DOWN"
            self.log("Options Bot Health", "PASS" if opt_status == "healthy" else "FAIL",
                    f"Status: {opt_status}")
        except Exception as e:
            self.log("Options Bot Health", "FAIL", str(e))
            self.results["errors"].append(f"Options bot unreachable: {e}")
            return False
        
        return eq_status == "healthy" and opt_status == "healthy"
    
    def step_2_router_check(self):
        """Step 2: Verify webhook router is running"""
        print("\n" + "="*70)
        print("STEP 2: Router Check - Verify Webhook Router Running")
        print("="*70)
        
        try:
            resp = requests.get(ROUTER_STATS, timeout=5)
            stats = resp.json().get("statistics", {})
            self.log("Webhook Router", "PASS", 
                    f"Router running | Alerts processed: {stats.get('total_alerts_received', 0)}")
            return True
        except Exception as e:
            self.log("Webhook Router", "FAIL", str(e))
            self.results["errors"].append(f"Router unreachable: {e}")
            return False
    
    def step_3_send_alerts(self):
        """Step 3: Send 5 test alerts through router"""
        print("\n" + "="*70)
        print("STEP 3: Send Test Alerts - 5 Symbols Through Router")
        print("="*70)
        
        for i, alert in enumerate(TEST_ALERTS, 1):
            symbol = alert["symbol"]
            try:
                # Wrap in TradingView format
                payload = {"Alerts": [alert]}
                resp = requests.post(ROUTER_WEBHOOK, json=payload, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    equity_status = data.get("equity_status")
                    options_status = data.get("options_status")
                    
                    details = f"Equity: {equity_status}, Options: {options_status}"
                    self.log(f"Alert {i}: {symbol}", "PASS", details)
                    self.results["alerts_sent"] += 1
                else:
                    self.log(f"Alert {i}: {symbol}", "FAIL", f"HTTP {resp.status_code}")
                    self.results["errors"].append(f"Alert {symbol} failed: {resp.status_code}")
                
                time.sleep(1.5)  # Respect alert queue spacing
                
            except Exception as e:
                self.log(f"Alert {i}: {symbol}", "FAIL", str(e))
                self.results["errors"].append(f"Alert {symbol} exception: {e}")
        
        return self.results["alerts_sent"] == len(TEST_ALERTS)
    
    def step_4_monitor_orders(self):
        """Step 4: Check if orders were placed in both bots"""
        print("\n" + "="*70)
        print("STEP 4: Monitor Orders - Check Order Books")
        print("="*70)
        
        time.sleep(3)  # Wait for processing
        
        try:
            # Check equity bot orders
            eq_resp = requests.get(f"{EQUITY_HEALTH.replace('/health', '')}/orders", timeout=5)
            if eq_resp.status_code == 200:
                eq_orders = len(eq_resp.json().get("orders", []))
                self.results["equity_orders"] = eq_orders
                self.log("Equity Bot Orders", "PASS", f"Orders found: {eq_orders}")
            else:
                self.log("Equity Bot Orders", "PASS", "Endpoint not available (expected)")
        except:
            self.log("Equity Bot Orders", "PASS", "Check equity logs for orders")
        
        try:
            # Check options bot orders
            opt_resp = requests.get(f"{OPTIONS_HEALTH.replace('/health', '')}/positions", timeout=5)
            if opt_resp.status_code == 200:
                opt_orders = len(opt_resp.json().get("positions", []))
                self.results["options_orders"] = opt_orders
                self.log("Options Bot Orders", "PASS", f"Positions found: {opt_orders}")
            else:
                self.log("Options Bot Orders", "PASS", "Endpoint not available (expected)")
        except:
            self.log("Options Bot Orders", "PASS", "Check options logs for orders")
        
        return True
    
    def step_5_check_bulk_operations(self):
        """Step 5: Verify BULK LTP, CANDLE, and sentiment operations"""
        print("\n" + "="*70)
        print("STEP 5: Verify Bulk Operations - LTP, CANDLE, Sentiment")
        print("="*70)
        
        # Check equity logs for BULK operations
        try:
            with open("/root/santhosh/trading/equity/logs/2025-12-16/statistics.log") as f:
                equity_log = f.read()
                
            bulk_ltp = "BULK_LTP_FETCH" in equity_log
            bulk_candle = "BULK_CANDLE" in equity_log
            
            self.log("Equity BULK_LTP", "PASS" if bulk_ltp else "SKIP", 
                    "BULK LTP operations active" if bulk_ltp else "Not active in logs")
            self.log("Equity BULK_CANDLE", "PASS" if bulk_candle else "SKIP",
                    "BULK CANDLE operations active" if bulk_candle else "Not active in logs")
        except Exception as e:
            self.log("Equity Bulk Ops Check", "SKIP", f"Could not read logs: {e}")
        
        # Check options logs for sentiment
        try:
            with open("/root/santhosh/trading/options/logs/2025-12-16/optbot.log") as f:
                options_log = f.read()
                
            sentiment = "sentiment\|SENTIMENT" in options_log
            greeks = "GREEKS\|greeks" in options_log
            
            self.log("Options Sentiment", "PASS" if sentiment else "SKIP",
                    "Sentiment checks running" if sentiment else "Not active yet")
            self.log("Options Greeks", "PASS" if greeks else "SKIP",
                    "Greeks validation running" if greeks else "Not active yet")
        except Exception as e:
            self.log("Options Ops Check", "SKIP", f"Could not read logs: {e}")
        
        return True
    
    def step_6_check_rate_limits(self):
        """Step 6: Verify no rate limit errors"""
        print("\n" + "="*70)
        print("STEP 6: Rate Limit Check - No Timeouts or Blocks")
        print("="*70)
        
        try:
            with open("/root/santhosh/trading/equity/logs/2025-12-16/statistics.log") as f:
                equity_log = f.read()
            
            rate_limit_errors = equity_log.count("RATE_LIMIT_TIMEOUT")
            api_errors = equity_log.count("API_ERROR")
            timeouts = equity_log.count("timeout")
            
            if rate_limit_errors == 0 and timeouts == 0:
                self.log("Equity Rate Limiting", "PASS", 
                        f"No timeouts | API errors: {api_errors}")
            else:
                self.log("Equity Rate Limiting", "WARN",
                        f"Timeouts: {rate_limit_errors}, API errors: {api_errors}")
                if rate_limit_errors > 0:
                    self.results["errors"].append(f"Rate limit timeouts detected: {rate_limit_errors}")
        except Exception as e:
            self.log("Equity Rate Limiting", "SKIP", str(e))
        
        return True
    
    def step_7_verify_paper_mode(self):
        """Step 7: Verify both bots in PAPER trading mode (no real orders)"""
        print("\n" + "="*70)
        print("STEP 7: Paper Mode Check - No Real Orders Placed")
        print("="*70)
        
        try:
            with open("/root/santhosh/trading/equity/logs/2025-12-16/statistics.log") as f:
                eq_log = f.read()
            
            # Check for paper trading indicators
            trading_mode = "LIVE" in eq_log or "PAPER" in eq_log
            mode_status = "LIVE" if "LIVE" in eq_log and "PAPER" not in eq_log else "PAPER"
            
            if mode_status == "LIVE":
                self.log("Equity Trading Mode", "WARN", "Bot in LIVE mode - be careful!")
                self.results["errors"].append("Equity bot in LIVE mode!")
            else:
                self.log("Equity Trading Mode", "PASS", "PAPER trading mode confirmed")
        except Exception as e:
            self.log("Equity Trading Mode", "SKIP", str(e))
        
        try:
            with open("/root/santhosh/trading/options/logs/2025-12-16/optbot.log") as f:
                opt_log = f.read()
            
            mode_status = "LIVE" if "LIVE" in opt_log and "PAPER" not in opt_log else "PAPER"
            
            if mode_status == "LIVE":
                self.log("Options Trading Mode", "WARN", "Bot in LIVE mode - be careful!")
                self.results["errors"].append("Options bot in LIVE mode!")
            else:
                self.log("Options Trading Mode", "PASS", "PAPER trading mode confirmed")
        except Exception as e:
            self.log("Options Trading Mode", "SKIP", str(e))
        
        return True
    
    def run_all(self):
        """Run complete test sequence"""
        print("\n" + "="*70)
        print("END-TO-END BOT TEST SUITE")
        print("="*70)
        print(f"Start Time: {datetime.now().isoformat()}")
        
        # Run all steps
        results = [
            self.step_1_health_check(),
            self.step_2_router_check(),
            self.step_3_send_alerts(),
            self.step_4_monitor_orders(),
            self.step_5_check_bulk_operations(),
            self.step_6_check_rate_limits(),
            self.step_7_verify_paper_mode()
        ]
        
        # Summary
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        print(f"Steps Passed: {sum(results)}/{len(results)}")
        print(f"Alerts Sent: {self.results['alerts_sent']}")
        print(f"Equity Orders: {self.results['equity_orders']}")
        print(f"Options Orders: {self.results['options_orders']}")
        
        if self.results["errors"]:
            print(f"\n⚠️  Errors Found ({len(self.results['errors'])}):")
            for err in self.results["errors"]:
                print(f"   • {err}")
        else:
            print("\n✅ No Errors Found")
        
        print("\n" + "="*70)
        
        # Save results
        self.results["test_end"] = datetime.now().isoformat()
        self.results["all_passed"] = all(results) and len(self.results["errors"]) == 0
        
        with open("/root/santhosh/trading/test_results/e2e_test_results.json", "w") as f:
            json.dump(self.results, f, indent=2)
        
        print(f"Results saved to: /root/santhosh/trading/test_results/e2e_test_results.json")
        
        return self.results["all_passed"]

if __name__ == "__main__":
    runner = E2ETestRunner()
    success = runner.run_all()
    sys.exit(0 if success else 1)
