#!/usr/bin/env python3
"""
Test the bulk LTP fetcher fix.
Verifies that fetch_bulk_ltp() now works correctly with ltpData() calls.
"""

import sys
import os

# Add eqcode to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'eqcode'))

# Mock bot_logging before importing bulk_ltp_fetcher
class MockLogging:
    @staticmethod
    def log_event(*args, **kwargs):
        pass

sys.modules['bot_logging'] = MockLogging()

# Now we can import with a mock log_event
import importlib.util
spec = importlib.util.spec_from_file_location("bulk_ltp_fetcher", 
                                               os.path.join(os.path.dirname(__file__), 'eqcode/bulk_ltp_fetcher.py'))
bulk_ltp_module = importlib.util.module_from_spec(spec)

# Create mock log_event
def mock_log_event(*args, **kwargs):
    pass

sys.modules['bulk_ltp_fetcher.bot_logging'] = type('module', (), {'log_event': mock_log_event})()

# Monkey patch before loading
import eqcode.bulk_ltp_fetcher as btf
btf.log_event = mock_log_event

from eqcode.bulk_ltp_fetcher import BulkLTPFetcher

# Mock SmartAPI object
class MockSmartAPI:
    def __init__(self):
        self.call_count = 0
        self.calls = []
    
    def ltpData(self, exchange, symbol, token):
        """Mock ltpData that simulates responses"""
        self.call_count += 1
        self.calls.append((exchange, symbol, token))
        
        # Simulate response format
        mock_data = {
            "NSE": {
                "11872": {"status": True, "data": {"ltp": 545.5}},  # GRANULES
                "3045": {"status": True, "data": {"ltp": 2945.5}},   # RELIANCE
                "4963": {"status": True, "data": {"ltp": 1850.3}},   # INFY
            },
            "NFO": {
                "35078": {"status": True, "data": {"ltp": 112.4}},
            }
        }
        
        # Return the mocked response for this token
        if exchange in mock_data and token in mock_data[exchange]:
            return mock_data[exchange][token]
        
        # Return None if not found (simulates missing token)
        return {"status": False}

def test_bulk_ltp_fetch():
    """Test bulk LTP fetch with mock API"""
    print("=" * 60)
    print("Testing Bulk LTP Fetcher Fix")
    print("=" * 60)
    
    # Create mock API
    mock_api = MockSmartAPI()
    
    # Create fetcher
    fetcher = BulkLTPFetcher(mock_api, cache_ttl_seconds=5)
    
    # Test 1: Fetch multiple tokens
    print("\n[Test 1] Fetching LTP for 3 NSE tokens...")
    token_dict = {
        "NSE": ["11872", "3045", "4963"],  # GRANULES, RELIANCE, INFY
    }
    
    result = fetcher.fetch_bulk_ltp(token_dict)
    
    print(f"  Requested: {token_dict}")
    print(f"  Result: {result}")
    print(f"  API Calls Made: {mock_api.call_count}")
    print(f"  Call Details: {mock_api.calls}")
    
    # Verify results
    expected_keys = ["NSE_11872", "NSE_3045", "NSE_4963"]
    for key in expected_keys:
        if key in result:
            print(f"  ✅ {key}: {result[key]}")
        else:
            print(f"  ❌ {key}: MISSING")
    
    # Test 2: Fetch mixed exchange tokens
    print("\n[Test 2] Fetching LTP for mixed exchanges...")
    mock_api = MockSmartAPI()  # Reset call count
    fetcher = BulkLTPFetcher(mock_api)
    
    token_dict = {
        "NSE": ["11872", "3045"],
        "NFO": ["35078"],
    }
    
    result = fetcher.fetch_bulk_ltp(token_dict)
    
    print(f"  Requested: {token_dict}")
    print(f"  Result: {result}")
    print(f"  API Calls Made: {mock_api.call_count}")
    
    expected_total = 3
    if mock_api.call_count == expected_total:
        print(f"  ✅ Correct number of API calls ({expected_total})")
    else:
        print(f"  ❌ Wrong number of API calls (expected {expected_total}, got {mock_api.call_count})")
    
    # Test 3: Empty token dict
    print("\n[Test 3] Empty token dict...")
    result = fetcher.fetch_bulk_ltp({})
    print(f"  Result: {result}")
    if result == {}:
        print(f"  ✅ Correctly returns empty dict")
    else:
        print(f"  ❌ Should return empty dict")
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)

if __name__ == "__main__":
    test_bulk_ltp_fetch()
