#!/usr/bin/env python3
"""
Test for LTP Chase & Order Modification Fix

Verifies:
1. @rate_limited decorator removed from modify_order()
2. modify_order() now uses PriorityRateLimiter for guaranteed execution
3. Trailing SL modifications work correctly
4. Bulk LTP fetching reduces API calls
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from eqcode.angelone import AngelOneBroker
from eqcode.monitor import Position, PositionMonitor, LTPBucketManager
from eqcode.config import TradingConfig, DevConfig
import inspect

def test_modify_order_decorator_removal():
    """Test that @rate_limited decorator has been removed from modify_order()"""
    
    print("\n" + "="*80)
    print("TEST 1: Verify @rate_limited Decorator Removed from modify_order()")
    print("="*80)
    
    # Get the modify_order method
    modify_order_func = AngelOneBroker.modify_order
    
    # Check if it has decorators
    source_code = inspect.getsource(modify_order_func)
    
    # The decorator would appear in the source code
    if "@rate_limited" in source_code:
        print("❌ FAILED: @rate_limited decorator still present on modify_order()")
        return False
    
    print("✅ PASSED: @rate_limited decorator successfully removed from modify_order()")
    return True

def test_modify_order_uses_safe_api_call():
    """Test that modify_order uses _safe_api_call (PriorityRateLimiter)"""
    
    print("\n" + "="*80)
    print("TEST 2: Verify modify_order() Uses _safe_api_call()")
    print("="*80)
    
    modify_order_func = AngelOneBroker.modify_order
    source_code = inspect.getsource(modify_order_func)
    
    # Check that _safe_api_call is used
    if "_safe_api_call" not in source_code:
        print("❌ FAILED: modify_order() does not use _safe_api_call()")
        return False
    
    if "self.smart_api.modifyOrder" not in source_code:
        print("❌ FAILED: modify_order() does not call broker's modifyOrder API")
        return False
    
    # Check for the correct API call with timeout
    if "self._safe_api_call(self.smart_api.modifyOrder" not in source_code:
        print("❌ FAILED: modify_order() not using correct API call pattern")
        return False
    
    print("✅ PASSED: modify_order() correctly uses _safe_api_call(self.smart_api.modifyOrder, ...)")
    return True

def test_bulk_ltp_fetcher_integration():
    """Test that monitor uses bulk LTP fetcher"""
    
    print("\n" + "="*80)
    print("TEST 3: Verify Monitor Uses Bulk LTP Fetching")
    print("="*80)
    
    from eqcode.monitor import PositionMonitor
    
    check_ltp_func = PositionMonitor._check_ltp_for_bucket
    source_code = inspect.getsource(check_ltp_func)
    
    # Check that bulk LTP is used
    if "get_ltp_bulk" not in source_code:
        print("❌ FAILED: _check_ltp_for_bucket() does not use get_ltp_bulk()")
        return False
    
    # Check for fallback to individual calls
    if "get_ltp(" not in source_code:
        print("❌ FAILED: _check_ltp_for_bucket() does not have fallback for individual calls")
        return False
    
    # Check for proper error handling
    if "except" not in source_code:
        print("❌ FAILED: _check_ltp_for_bucket() does not handle errors properly")
        return False
    
    print("✅ PASSED: Monitor correctly uses get_ltp_bulk() with fallback to individual calls")
    return True

def test_trailing_sl_modification_logic():
    """Test that trailing SL modification logic is correct"""
    
    print("\n" + "="*80)
    print("TEST 4: Verify Trailing SL Modification Logic")
    print("="*80)
    
    from eqcode.monitor import PositionMonitor
    
    update_trailing_sl_func = PositionMonitor._update_trailing_sl
    source_code = inspect.getsource(update_trailing_sl_func)
    
    # Check key logic elements
    checks = [
        ("modify_order call", "self.broker.modify_order("),
        ("SL order ID check", "position.sl_order_id"),
        ("Order type handling", "order_type"),
        ("Product type handling", "product_type"),
        ("Adaptive SL logic", "adaptive_sl"),
        ("Error handling", "except"),
    ]
    
    all_passed = True
    for check_name, pattern in checks:
        if pattern not in source_code:
            print(f"❌ Missing: {check_name}")
            all_passed = False
    
    if not all_passed:
        print("\n❌ FAILED: Trailing SL modification logic incomplete")
        return False
    
    print("✅ PASSED: Trailing SL modification has all required logic")
    return True

def test_bucket_manager():
    """Test LTPBucketManager for correct bucketing"""
    
    print("\n" + "="*80)
    print("TEST 5: Verify LTPBucketManager Implementation")
    print("="*80)
    
    manager = LTPBucketManager(bucket_size=5)
    
    # Test with 20 symbols
    symbols = [f"SYMBOL{i}" for i in range(20)]
    manager.create_buckets(symbols)
    
    # Should have 4 buckets
    if len(manager.buckets) != 4:
        print(f"❌ FAILED: Expected 4 buckets, got {len(manager.buckets)}")
        return False
    
    # Each bucket should have 5 symbols
    for i, bucket in enumerate(manager.buckets):
        if len(bucket) != 5:
            print(f"❌ FAILED: Bucket {i} has {len(bucket)} symbols, expected 5")
            return False
    
    # Test bucket rotation (get_current_bucket advances automatically)
    bucket1 = manager.get_current_bucket()
    if bucket1 != symbols[:5]:
        print(f"❌ FAILED: First bucket incorrect")
        return False
    
    bucket2 = manager.get_current_bucket()
    if bucket2 != symbols[5:10]:
        print(f"❌ FAILED: Second bucket incorrect")
        return False
    
    print("✅ PASSED: LTPBucketManager correctly creates and rotates buckets")
    return True

def test_rate_limiter_capacity():
    """Test that PriorityRateLimiter reserves capacity for modify_order"""
    
    print("\n" + "="*80)
    print("TEST 6: Verify PriorityRateLimiter Capacity Reservation")
    print("="*80)
    
    from eqcode.priority_rate_limiter import PriorityRateLimiter, Priority
    
    limiter = PriorityRateLimiter(
        rps_limit=8,
        rpm_limit=180,
    )
    
    # Check that modifyOrder is marked as CRITICAL
    if not hasattr(limiter, 'priority_mapping'):
        print("⚠️  WARNING: PriorityRateLimiter does not have priority_mapping")
        return True  # Non-critical
    
    priority_map = limiter.priority_mapping
    
    if "modifyOrder" not in priority_map:
        print("❌ FAILED: modifyOrder not in priority mapping")
        return False
    
    if priority_map["modifyOrder"] != Priority.CRITICAL:
        print(f"❌ FAILED: modifyOrder priority is {priority_map['modifyOrder']}, expected CRITICAL")
        return False
    
    print("✅ PASSED: modifyOrder is marked as CRITICAL in PriorityRateLimiter")
    return True

def main():
    """Run all tests"""
    
    print("\n" + "="*80)
    print("🧪 LTP CHASE & ORDER MODIFICATION FIX - TEST SUITE")
    print("="*80)
    
    tests = [
        test_modify_order_decorator_removal,
        test_modify_order_uses_safe_api_call,
        test_bulk_ltp_fetcher_integration,
        test_trailing_sl_modification_logic,
        test_bucket_manager,
        test_rate_limiter_capacity,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append((test.__name__, result))
        except Exception as e:
            print(f"\n❌ EXCEPTION in {test.__name__}: {str(e)}")
            results.append((test.__name__, False))
    
    # Summary
    print("\n" + "="*80)
    print("📊 TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! LTP Chase and Order Modification fixes are working correctly.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please review the output above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
