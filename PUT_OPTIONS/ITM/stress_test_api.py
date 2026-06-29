#!/usr/bin/env python3
"""
Stress test for options webhook API server.
Tests throughput, latency, and error handling under high alert volume.
"""

import json
import time
import requests
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple

def send_alert(url: str, alert: Dict, timeout: int = 5) -> Tuple[str, float, int, str]:
    """Send single alert and measure response time"""
    try:
        start_time = time.time()
        response = requests.post(
            url,
            json=alert,
            timeout=timeout,
            headers={'Content-Type': 'application/json'}
        )
        elapsed = time.time() - start_time
        return 'success', elapsed, response.status_code, response.text[:100]
    except requests.Timeout:
        return 'timeout', timeout, 0, 'Request timed out'
    except Exception as e:
        return 'error', 0, 0, str(e)[:100]

def test_single_alerts(url: str, num_alerts: int = 10, workers: int = 1) -> Dict:
    """Test single alerts with concurrent requests"""
    print(f"\n📊 TEST 1: Single Alert Burst ({num_alerts} alerts, {workers} workers)")
    print("=" * 80)
    
    alerts = []
    for i in range(num_alerts):
        alerts.append({
            'symbol': 'SBIN',
            'action': 'BUY',
            'price': 500 + i,
            'timestamp': datetime.now().isoformat()
        })
    
    results = {
        'total': len(alerts),
        'successful': 0,
        'timeout': 0,
        'error': 0,
        'times': [],
        'start': datetime.now()
    }
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(send_alert, url, alert) for alert in alerts]
        
        for idx, future in enumerate(as_completed(futures), 1):
            status, elapsed, code, msg = future.result()
            results['times'].append(elapsed)
            
            if status == 'success':
                results['successful'] += 1
                print(f"  [{idx:3d}] ✅ {elapsed:.3f}s | HTTP {code}")
            elif status == 'timeout':
                results['timeout'] += 1
                print(f"  [{idx:3d}] ⏱️  {elapsed:.3f}s | TIMEOUT")
            else:
                results['error'] += 1
                print(f"  [{idx:3d}] ❌ {elapsed:.3f}s | ERROR: {msg}")
    
    results['elapsed'] = (datetime.now() - results['start']).total_seconds()
    results['throughput'] = results['total'] / results['elapsed'] if results['elapsed'] > 0 else 0
    results['avg_time'] = sum(results['times']) / len(results['times']) if results['times'] else 0
    results['min_time'] = min(results['times']) if results['times'] else 0
    results['max_time'] = max(results['times']) if results['times'] else 0
    
    print(f"\n📈 Results:")
    print(f"   Total: {results['total']} | Success: {results['successful']} | Timeout: {results['timeout']} | Error: {results['error']}")
    print(f"   Total Time: {results['elapsed']:.2f}s | Throughput: {results['throughput']:.1f} alerts/sec")
    print(f"   Latency - Avg: {results['avg_time']:.3f}s | Min: {results['min_time']:.3f}s | Max: {results['max_time']:.3f}s")
    
    return results

def test_bulk_alerts(url: str, num_alerts: int = 10, batch_size: int = 5) -> Dict:
    """Test sending multiple alerts in single request (burst mode)"""
    print(f"\n📊 TEST 2: Bulk Alert (Single Request with {num_alerts} alerts)")
    print("=" * 80)
    
    alerts = []
    for i in range(num_alerts):
        alerts.append({
            'symbol': f'NIFTY{50+i}',
            'action': 'SELL',
            'price': 100 + i,
            'timestamp': datetime.now().isoformat()
        })
    
    start_time = time.time()
    status, elapsed, code, msg = send_alert(url, alerts, timeout=30)
    elapsed = time.time() - start_time
    
    print(f"\n📈 Results:")
    print(f"   Status: {status.upper()} | Time: {elapsed:.3f}s | HTTP {code}")
    print(f"   Throughput: {num_alerts/elapsed:.1f} alerts/sec")
    print(f"   Response: {msg}")
    
    return {
        'total': num_alerts,
        'status': status,
        'elapsed': elapsed,
        'throughput': num_alerts / elapsed if elapsed > 0 else 0,
        'code': code
    }

def test_sustained_load(url: str, duration: int = 30, workers: int = 5) -> Dict:
    """Test sustained alert load over time"""
    print(f"\n📊 TEST 3: Sustained Load ({duration}s duration, {workers} workers)")
    print("=" * 80)
    
    results = {
        'total': 0,
        'successful': 0,
        'timeout': 0,
        'error': 0,
        'times': [],
        'start': datetime.now()
    }
    
    running = True
    alert_id = 0
    
    def generate_alerts():
        nonlocal alert_id
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []
            
            while running and (datetime.now() - results['start']).total_seconds() < duration:
                # Generate alert
                alert = {
                    'symbol': ['SBIN', 'INFY', 'TCS', 'RELIANCE', 'HDFC'][alert_id % 5],
                    'action': ['BUY', 'SELL'][alert_id % 2],
                    'price': 100 + (alert_id % 50),
                    'timestamp': datetime.now().isoformat()
                }
                alert_id += 1
                
                # Submit for processing
                future = executor.submit(send_alert, url, alert)
                futures.append(future)
                
                # Print progress every 5 seconds
                if alert_id % 20 == 0:
                    elapsed = (datetime.now() - results['start']).total_seconds()
                    print(f"   [{elapsed:.1f}s] Sent {alert_id} alerts...")
                
                time.sleep(0.1)  # Small delay between submissions (~10 alerts/sec)
            
            # Collect results
            for future in as_completed(futures):
                status, elapsed, code, msg = future.result()
                results['times'].append(elapsed)
                results['total'] += 1
                
                if status == 'success':
                    results['successful'] += 1
                elif status == 'timeout':
                    results['timeout'] += 1
                else:
                    results['error'] += 1
    
    generate_alerts()
    running = False
    results['elapsed'] = (datetime.now() - results['start']).total_seconds()
    results['throughput'] = results['total'] / results['elapsed'] if results['elapsed'] > 0 else 0
    results['avg_time'] = sum(results['times']) / len(results['times']) if results['times'] else 0
    
    print(f"\n📈 Results:")
    print(f"   Total: {results['total']} | Success: {results['successful']} | Timeout: {results['timeout']} | Error: {results['error']}")
    print(f"   Duration: {results['elapsed']:.2f}s | Throughput: {results['throughput']:.1f} alerts/sec")
    print(f"   Avg Latency: {results['avg_time']:.3f}s")
    
    return results

def main():
    """Run all stress tests"""
    API_URL = "http://127.0.0.1:8081/webhook/options"
    
    # Check if API is reachable
    print("\n🔍 Checking API connectivity...")
    try:
        response = requests.get("http://127.0.0.1:8081/health", timeout=5)
        print(f"✅ API is reachable | Status: {response.status_code}")
    except Exception as e:
        print(f"❌ API is not reachable: {e}")
        print("   Start the bot first: cd /root/santhosh/trading/options && python3 main.py")
        return
    
    print("\n" + "=" * 80)
    print("🚀 OPTIONS WEBHOOK API STRESS TEST")
    print("=" * 80)
    
    # Test 1: Single alerts with varying concurrency
    print("\n🔴 PHASE 1: SINGLE ALERT CONCURRENCY TESTS")
    test1_seq = test_single_alerts(API_URL, num_alerts=10, workers=1)
    test1_par = test_single_alerts(API_URL, num_alerts=10, workers=5)
    test1_burst = test_single_alerts(API_URL, num_alerts=10, workers=10)
    
    # Test 2: Bulk alerts
    print("\n🟡 PHASE 2: BULK ALERT TEST")
    test2 = test_bulk_alerts(API_URL, num_alerts=20)
    
    # Test 3: Sustained load
    print("\n🟢 PHASE 3: SUSTAINED LOAD TEST")
    test3 = test_sustained_load(API_URL, duration=30, workers=5)
    
    # Summary
    print("\n" + "=" * 80)
    print("📊 SUMMARY")
    print("=" * 80)
    print(f"\nTest 1a (Sequential):")
    print(f"  Throughput: {test1_seq['throughput']:.1f} alerts/sec")
    print(f"  Avg Latency: {test1_seq['avg_time']:.3f}s")
    
    print(f"\nTest 1b (5 workers):")
    print(f"  Throughput: {test1_par['throughput']:.1f} alerts/sec")
    print(f"  Avg Latency: {test1_par['avg_time']:.3f}s")
    
    print(f"\nTest 1c (10 workers):")
    print(f"  Throughput: {test1_burst['throughput']:.1f} alerts/sec")
    print(f"  Avg Latency: {test1_burst['avg_time']:.3f}s")
    
    print(f"\nTest 2 (Bulk request):")
    print(f"  Throughput: {test2['throughput']:.1f} alerts/sec")
    
    print(f"\nTest 3 (Sustained 30s):")
    print(f"  Throughput: {test3['throughput']:.1f} alerts/sec")
    print(f"  Success Rate: {test3['successful']}/{test3['total']} ({100*test3['successful']/test3['total']:.1f}%)")
    
    print("\n" + "=" * 80)
    print("✅ STRESS TEST COMPLETE")
    print("=" * 80)

if __name__ == '__main__':
    main()
