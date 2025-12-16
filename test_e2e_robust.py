#!/usr/bin/env python3
"""
Robust E2E Test - With proper timing and queue handling
"""

import requests
import time
import json
from datetime import datetime

EQUITY_HEALTH = "http://127.0.0.1:8080/health"
OPTIONS_HEALTH = "http://127.0.0.1:8081/health"
ROUTER_STATS = "http://127.0.0.1/stats"
ROUTER_WEBHOOK = "http://127.0.0.1/webhook"

TEST_ALERTS = [
    {"symbol": "NIFTY", "action": "BUY", "price": "25000.0"},
    {"symbol": "TITAN", "action": "BUY", "price": "3900.0"},
]

print("="*70)
print("ROBUST END-TO-END TEST")
print("="*70)

# Check health
print("\n✓ Checking bot health...")
eq_health = requests.get(EQUITY_HEALTH, timeout=5).json()
opt_health = requests.get(OPTIONS_HEALTH, timeout=5).json()

print(f"  Equity: {eq_health['status']} (LIVE)" if "LIVE" in str(eq_health) else f"  Equity: {eq_health['status']}")
print(f"  Options: {opt_health['status']} (PAPER)")

# Get initial stats
stats_before = requests.get(ROUTER_STATS, timeout=5).json()["statistics"]
alerts_before = stats_before["total_alerts_received"]

print(f"\n✓ Alerts already processed: {alerts_before}")

# Send alerts slowly to avoid queue buildup
print("\n✓ Sending 2 test alerts with 3s spacing...")
for i, alert in enumerate(TEST_ALERTS, 1):
    payload = {"Alerts": [alert]}
    print(f"  [{i}] Sending {alert['symbol']}...", end=" ")
    
    try:
        resp = requests.post(ROUTER_WEBHOOK, json=payload, timeout=15)  # Longer timeout
        print(f"HTTP {resp.status_code}")
    except Exception as e:
        print(f"ERROR: {e}")
    
    time.sleep(3)  # Wait before next alert

# Check final stats
time.sleep(5)
stats_after = requests.get(ROUTER_STATS, timeout=5).json()["statistics"]
alerts_after = stats_after["total_alerts_received"]

print(f"\n✓ Alerts processed total: {alerts_after}")
print(f"  New alerts in this test: {alerts_after - alerts_before}")

# Check robot positions
print("\n✓ Checking positions...")

try:
    eq_resp = requests.get("http://127.0.0.1:8080/orders", timeout=5)
    if eq_resp.status_code == 200:
        eq_orders = eq_resp.json()
        print(f"  Equity orders endpoint available")
except:
    print(f"  Equity orders: check logs manually")

try:
    opt_positions = opt_health.get("open_positions", 0)
    print(f"  Options positions: {opt_positions}")
except:
    print(f"  Options positions: check logs")

print("\n✅ Test Complete")
