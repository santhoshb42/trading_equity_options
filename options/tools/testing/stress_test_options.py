#!/usr/bin/env python3
"""
Options Bot Stress Test Simulator

Generates burst alert patterns for options trading:
- Multiple symbols (BANKNIFTY, NIFTY, FINNIFTY)
- Mixed directional signals (BUY CE, SELL PE)
- Concurrent alert flooding
- Rate limit analysis
"""

import json
import random
import time
import threading
import queue
import requests
import argparse
from datetime import datetime
from pathlib import Path

# Configuration
DEFAULT_URL = "http://127.0.0.1:8081/webhook/options"
UNDERLYINGS = ["BANKNIFTY", "NIFTY", "FINNIFTY"]

# Burst patterns
BURSTS = [
    {"name": "morning-burst", "size": 8, "duration_s": 12},
    {"name": "mid-burst", "size": 6, "duration_s": 10},
    {"name": "late-burst", "size": 7, "duration_s": 12},
]

def gen_price(underlying: str) -> float:
    """Generate mock price for underlying"""
    prices = {
        'BANKNIFTY': random.uniform(46000, 48000),
        'NIFTY': random.uniform(23000, 24000),
        'FINNIFTY': random.uniform(21500, 22500)
    }
    return prices.get(underlying, 20000)

# =============================================================================
# Alert Producer (Options-specific)
# =============================================================================

class OptionsAlertProducer:
    """Generates realistic options trading alerts"""
    
    def __init__(self, underlyings, bursts):
        self.underlyings = underlyings
        self.bursts = bursts
        self.out_q = queue.Queue()
    
    def produce(self):
        """Produce alert bursts"""
        # Warmup
        print("🔥 Generating warmup alerts...")
        for u in random.sample(self.underlyings, min(2, len(self.underlyings))):
            if random.random() < 0.6:
                self.out_q.put(self._make_buy_alert(u))
            else:
                self.out_q.put(self._make_sell_alert(u))
        
        time.sleep(1)
        
        # Bursts
        print(f"📊 Generating {len(self.bursts)} burst patterns...")
        for burst in self.bursts:
            candidates = self.underlyings
            start = time.time()
            
            print(f"   Burst: {burst['name']} ({burst['size']} alerts over {burst['duration_s']}s)")
            
            while time.time() - start < burst['duration_s']:
                u = random.choice(candidates)
                
                if random.random() < 0.65:
                    self.out_q.put(self._make_buy_alert(u))
                else:
                    self.out_q.put(self._make_sell_alert(u))
                
                time.sleep(random.uniform(0.3, 0.8))
    
    def _make_buy_alert(self, underlying):
        """Generate BUY alert (Long CE)"""
        return {
            "symbol": underlying,
            "action": "BUY",
            "price": gen_price(underlying),
            "score": random.randint(85, 99),
            "confidence": random.randint(90, 99),
            "verdict": 1
        }
    
    def _make_sell_alert(self, underlying):
        """Generate SELL alert (Long PE)"""
        return {
            "symbol": underlying,
            "action": "SELL",
            "price": gen_price(underlying),
            "score": random.randint(85, 99),
            "confidence": random.randint(85, 95),
            "verdict": 0
        }

# =============================================================================
# Alert Sender (Options-specific)
# =============================================================================

class OptionsAlertSender:
    """Sends options alerts to webhook"""
    
    def __init__(self, api_url, in_q, concurrency=6):
        self.api_url = api_url
        self.in_q = in_q
        self.concurrency = concurrency
        self.results = []
        self.threads = []
        self.stop_flag = False
    
    def start(self):
        for _ in range(self.concurrency):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self.threads.append(t)
    
    def stop(self):
        self.stop_flag = True
        for t in self.threads:
            t.join(timeout=1)
    
    def _worker(self):
        session = requests.Session()
        while not self.stop_flag or not self.in_q.empty():
            try:
                alert = self.in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            
            try:
                resp = session.post(self.api_url, json=alert, timeout=5)
                status = resp.status_code
                data = {}
                try:
                    data = resp.json()
                except:
                    pass
                
                self.results.append({
                    "ts": datetime.now().isoformat(),
                    "alert": alert,
                    "status": status,
                    "resp": data
                })
            except Exception as e:
                self.results.append({
                    "ts": datetime.now().isoformat(),
                    "alert": alert,
                    "status": 0,
                    "error": str(e)
                })
            finally:
                self.in_q.task_done()

# =============================================================================
# Main Stress Test
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Options bot stress test")
    parser.add_argument("--url", default=DEFAULT_URL, help="Webhook URL")
    parser.add_argument("--underlyings", type=int, default=3, help="Number of underlyings to use")
    parser.add_argument("--concurrency", type=int, default=6, help="Concurrent workers")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    print(f"""
╔════════════════════════════════════════════════════════╗
║         OPTIONS BOT STRESS TEST SIMULATOR              ║
║     Testing: {args.url}
║     Concurrency: {args.concurrency} workers              
╚════════════════════════════════════════════════════════╝
    """)
    
    # Produce alerts
    producer = OptionsAlertProducer(
        underlyings=UNDERLYINGS[:args.underlyings],
        bursts=BURSTS
    )
    
    producer_thread = threading.Thread(target=producer.produce, daemon=True)
    producer_thread.start()
    
    # Send alerts
    sender = OptionsAlertSender(args.url, producer.out_q, args.concurrency)
    sender.start()
    
    # Wait for completion
    producer_thread.join()
    producer.out_q.join()
    sender.stop()
    
    # Analyze results
    print("\n" + "="*60)
    print("TEST RESULTS")
    print("="*60)
    
    total = len(sender.results)
    ok = sum(1 for r in sender.results if r['status'] == 200)
    failed = total - ok
    
    buy_count = sum(1 for r in sender.results if r['alert'].get('action') == 'BUY')
    sell_count = total - buy_count
    
    print(f"Total Alerts: {total}")
    print(f"  ✅ Success (200): {ok} ({ok*100//total if total else 0}%)")
    print(f"  ❌ Failed: {failed}")
    print(f"  📈 BUY alerts: {buy_count}")
    print(f"  📉 SELL alerts: {sell_count}")
    
    # Show sample failures
    failures = [r for r in sender.results if r['status'] != 200]
    if failures:
        print(f"\nSample Failures (first 5):")
        for r in failures[:5]:
            print(f"  {r['alert']['symbol']}: {r.get('resp', {}).get('error', 'Unknown error')}")
    
    # Success rate
    print(f"\n📊 Test Conclusion:")
    if ok == total:
        print(f"   ✅ PERFECT: All {total} alerts processed successfully")
    elif ok >= total * 0.8:
        print(f"   ✅ GOOD: {ok}/{total} success rate ({ok*100//total}%)")
    else:
        print(f"   ⚠️  LOW: Only {ok}/{total} success ({ok*100//total}%)")
    
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
