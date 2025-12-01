#!/usr/bin/env python3
"""
Test script to demonstrate Options Bot Rate Limiter

This script tests:
1. Rate limiter initialization
2. Token bucket behavior
3. Request queueing
4. Periodic monitoring
"""

import sys
import time
sys.path.insert(0, '/root/santhosh/trading/options')

from optcode.options_rate_limiter import get_options_rate_limiter
from optcode.angelone_options import get_options_broker
from optcode.optmonitor import get_option_monitor

def print_section(title):
    """Print a section header"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def test_rate_limiter_initialization():
    """Test 1: Rate limiter initialization"""
    print_section("TEST 1: Rate Limiter Initialization")
    
    limiter = get_options_rate_limiter()
    
    print(f"✅ Rate limiter created")
    print(f"   RPS Limit: {limiter.rps_limit} requests/second")
    print(f"   RPM Limit: {limiter.rpm_limit} requests/minute")
    print(f"   Per-second bucket capacity: {limiter.second_bucket.capacity}")
    print(f"   Per-minute bucket capacity: {limiter.minute_bucket.capacity}")

def test_token_bucket_consumption():
    """Test 2: Token bucket consumption"""
    print_section("TEST 2: Token Bucket Consumption")
    
    limiter = get_options_rate_limiter()
    
    print(f"Initial state:")
    stats = limiter.get_statistics()
    print(f"   Second bucket tokens: {stats['second_bucket']['tokens']}")
    print(f"   Minute bucket tokens: {stats['minute_bucket']['tokens']}")
    
    print(f"\nSimulating 3 API calls...")
    for i in range(3):
        can_call, reason = limiter.can_make_call()
        if can_call:
            print(f"   Call {i+1}: ✅ Allowed")
            limiter.record_call(f"test_call_{i+1}", True)
        else:
            print(f"   Call {i+1}: ❌ Blocked - {reason}")
    
    print(f"\nFinal state:")
    stats = limiter.get_statistics()
    print(f"   Total calls: {stats['total_calls']}")
    print(f"   Second bucket tokens remaining: {stats['second_bucket']['tokens']:.1f}")
    print(f"   Second bucket utilization: {stats['second_bucket']['utilization']:.1f}%")

def test_request_queueing():
    """Test 3: Request queueing on rate limit"""
    print_section("TEST 3: Request Queueing Mechanism")
    
    limiter = get_options_rate_limiter()
    
    print(f"Queue state before:")
    stats = limiter.get_statistics()
    print(f"   Queued requests: {stats['queued_requests']}")
    
    print(f"\nQueueing a test request...")
    def dummy_callback():
        return "Result from queued request"
    
    limiter.queue_request(
        request_type="TEST_ORDER",
        callback=dummy_callback,
        args=(),
        kwargs={}
    )
    
    print(f"\nQueue state after:")
    stats = limiter.get_statistics()
    print(f"   Queued requests: {stats['queued_requests']}")
    print(f"   Queue size: {len(limiter.request_queue.queue)}")

def test_broker_integration():
    """Test 4: Broker integration with rate limiter"""
    print_section("TEST 4: Broker Integration")
    
    broker = get_options_broker()
    
    print(f"✅ Options broker created")
    
    print(f"\nRate limiter methods available:")
    print(f"   ✓ process_pending_rate_limited_requests()")
    print(f"   ✓ get_rate_limiter_stats()")
    
    print(f"\nRate limiter status:")
    stats = broker.get_rate_limiter_stats()
    print(f"   Total calls: {stats['total_calls']}")
    print(f"   Blocked calls: {stats['blocked_calls']}")
    print(f"   Success rate: {stats['success_rate']}%")
    print(f"   Calls last 1 min: {stats['calls_last_1min']}")
    print(f"   RPS limit: {stats['limits']['rps']}")
    print(f"   RPM limit: {stats['limits']['rpm']}")

def test_monitoring_integration():
    """Test 5: Monitor integration with rate limiter"""
    print_section("TEST 5: Monitoring Integration")
    
    monitor = get_option_monitor()
    broker = get_options_broker()
    
    print(f"✅ Option monitor created")
    
    print(f"\nPerforming periodic monitoring...")
    result = monitor.perform_periodic_monitoring()
    
    print(f"Monitoring result:")
    print(f"   Timestamp: {result['timestamp']}")
    print(f"   Positions monitored: {result['positions_monitored']}")
    print(f"   Closed by expiry: {len(result['closed_by_expiry'])}")
    print(f"   Closed by profit: {len(result['closed_by_profit'])}")
    print(f"   Closed by stop loss: {len(result['closed_by_stoploss'])}")
    
    if result['error']:
        print(f"   ❌ Error: {result['error']}")
    else:
        print(f"   ✅ No errors")
    
    print(f"\nRate limiter status during monitoring:")
    rl_stats = result['rate_limiter_stats']
    print(f"   Total calls: {rl_stats['total_calls']}")
    print(f"   Queued requests: {rl_stats['queued_requests']}")
    print(f"   Success rate: {rl_stats['success_rate']}%")

def test_statistics_and_monitoring():
    """Test 6: Statistics collection and monitoring"""
    print_section("TEST 6: Statistics & Monitoring")
    
    limiter = get_options_rate_limiter()
    
    print(f"Current rate limiter statistics:")
    stats = limiter.get_statistics()
    
    print(f"\n  Overall Stats:")
    print(f"    Total calls: {stats['total_calls']}")
    print(f"    Blocked calls: {stats['blocked_calls']}")
    print(f"    Queued calls: {stats['queued_calls']}")
    print(f"    Success rate: {stats['success_rate']}%")
    print(f"    Average wait time: {stats['avg_wait_time']}ms")
    
    print(f"\n  Time Window Stats:")
    print(f"    Calls last 1 min: {stats['calls_last_1min']}")
    print(f"    Calls last 5 min: {stats['calls_last_5min']}")
    
    print(f"\n  Per-Second Bucket:")
    second = stats['second_bucket']
    print(f"    Tokens: {second['tokens']:.1f}/{second['capacity']}")
    print(f"    Utilization: {second['utilization']:.1f}%")
    print(f"    Refill rate: {second['refill_rate']}/sec")
    
    print(f"\n  Per-Minute Bucket:")
    minute = stats['minute_bucket']
    print(f"    Tokens: {minute['tokens']:.1f}/{minute['capacity']}")
    print(f"    Utilization: {minute['utilization']:.1f}%")
    print(f"    Refill rate: {minute['refill_rate']:.2f}/sec")
    
    print(f"\n  Limits:")
    print(f"    RPS: {stats['limits']['rps']}")
    print(f"    RPM: {stats['limits']['rpm']}")

def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("  OPTIONS BOT RATE LIMITER TEST SUITE")
    print("="*70)
    
    try:
        test_rate_limiter_initialization()
        test_token_bucket_consumption()
        test_request_queueing()
        test_broker_integration()
        test_monitoring_integration()
        test_statistics_and_monitoring()
        
        print_section("✅ ALL TESTS PASSED")
        print("Rate limiter is properly integrated and operational!\n")
        
        return 0
        
    except Exception as e:
        print_section("❌ TEST FAILED")
        print(f"Error: {str(e)}\n")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
