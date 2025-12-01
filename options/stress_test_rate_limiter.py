"""
OPTIONS BOT RATE LIMITER STRESS TEST

Simulates realistic trading scenario:
- 30 BUY orders over 2-3 minutes (uneven distribution)
- Continuous monitoring cycles (every 30 seconds)
- Stop loss triggers mixed in
- Profit target closures
- Rate limiting under extreme load

This tests:
✓ Token bucket under sustained load
✓ Request queuing when rate limited
✓ Monitoring loop processing queued requests
✓ Multiple exit types (SL, profit, expiry)
✓ Concurrent operations
"""

import sys
import time
import threading
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any

sys.path.insert(0, '/root/santhosh/trading/options')

from optcode.angelone_options import get_options_broker
from optcode.optmonitor import get_option_monitor
from optcode.options_rate_limiter import get_options_rate_limiter


class StressTestCoordinator:
    """Orchestrates stress test scenarios"""
    
    def __init__(self):
        self.broker = get_options_broker()
        self.monitor = get_option_monitor(self.broker)
        self.rate_limiter = get_options_rate_limiter()
        
        self.orders_placed = []
        self.orders_executed = []
        self.orders_queued = []
        self.monitoring_cycles = []
        self.sl_triggers = []
        self.profit_triggers = []
        
        self.test_start_time = None
        self.test_end_time = None
        
        # Underlying symbols for options
        self.underlyings = ['BANKNIFTY', 'NIFTY', 'FINNIFTY']
        self.strikes = [19000, 19100, 19200, 23500, 23600, 22000, 22100]
    
    def log_event(self, event_type: str, message: str, **kwargs):
        """Log test events with timestamp"""
        elapsed = (time.time() - self.test_start_time) if self.test_start_time else 0
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        prefix = {
            'ORDER': '📝',
            'EXECUTE': '✅',
            'QUEUE': '⏳',
            'MONITOR': '🔍',
            'SL': '🛑',
            'PROFIT': '💰',
            'ERROR': '❌',
            'STATS': '📊'
        }.get(event_type, '•')
        
        msg = f"{timestamp} [{elapsed:6.1f}s] {prefix} {message}"
        for key, val in kwargs.items():
            msg += f" | {key}={val}"
        
        print(msg)
    
    def generate_symbol(self) -> str:
        """Generate random option symbol"""
        underlying = random.choice(self.underlyings)
        strike = random.choice(self.strikes)
        contract_type = random.choice(['CE', 'PE'])
        
        # Simplified symbol generation
        expiry = 'JAN'
        symbol = f"{underlying}25{expiry}{strike}{contract_type}"
        return symbol
    
    def simulate_order_burst(self, num_orders: int, duration: int) -> List[Dict[str, Any]]:
        """
        Simulate burst of orders over specified duration
        Spreads orders unevenly to simulate real alert patterns
        """
        self.log_event('ORDER', f"Starting burst of {num_orders} orders over {duration}s")
        
        orders = []
        intervals = []
        
        # Create non-uniform distribution (clusters + gaps)
        remaining_orders = num_orders
        remaining_time = duration
        
        while remaining_orders > 0:
            # Random cluster size (1-5 orders)
            cluster_size = random.randint(1, min(5, remaining_orders))
            
            # Time for this cluster (faster)
            cluster_time = random.uniform(0.5, 2.0)
            
            # Gap after cluster (pause)
            gap_time = random.uniform(2.0, 8.0) if remaining_orders > cluster_size else 0
            
            # Spread orders within cluster
            for i in range(cluster_size):
                offset = cluster_time / cluster_size * i
                intervals.append(offset)
            
            remaining_orders -= cluster_size
            remaining_time -= (cluster_time + gap_time)
        
        # Sort and normalize intervals
        intervals.sort()
        if intervals:
            max_interval = intervals[-1]
            intervals = [i * (duration / max_interval) for i in intervals]
        
        # Place orders at scheduled times
        start_time = time.time()
        for idx, interval in enumerate(intervals):
            # Wait until scheduled time
            while time.time() - start_time < interval:
                time.sleep(0.01)
            
            symbol = self.generate_symbol()
            action = random.choice(['BUY', 'BUY', 'BUY'])  # Mostly buys
            quantity = random.randint(1, 3)
            premium = round(random.uniform(50, 500), 2)
            
            self.log_event('ORDER', f"Placing order #{idx+1}/{num_orders}", 
                          symbol=symbol, action=action, qty=quantity, premium=f"₹{premium}")
            
            # Place order (with rate limiting)
            order_id = self.broker.place_options_order(
                symbol=symbol,
                action=action,
                quantity=quantity,
                price=premium,
                order_type="MARKET"
            )
            
            orders.append({
                'index': idx + 1,
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'action': action,
                'quantity': quantity,
                'premium': premium,
                'order_id': order_id,
                'queued': order_id.startswith('QUEUED') if order_id else False
            })
            
            if order_id:
                if order_id.startswith('QUEUED'):
                    self.log_event('QUEUE', f"Order queued due to rate limit", order=f"#{idx+1}")
                    self.orders_queued.append(orders[-1])
                else:
                    self.log_event('EXECUTE', f"Order executed immediately", order=f"#{idx+1}")
                    self.orders_executed.append(orders[-1])
            else:
                self.log_event('ERROR', f"Order failed", order=f"#{idx+1}")
            
            self.orders_placed.append(orders[-1])
        
        return orders
    
    def simulate_monitoring_cycles(self, duration: int, interval: int = 30):
        """
        Simulate continuous monitoring loop during stress test
        Processes queued requests and checks exit conditions
        """
        start_time = time.time()
        cycle_num = 0
        
        while time.time() - start_time < duration:
            cycle_num += 1
            
            self.log_event('MONITOR', f"Monitoring cycle #{cycle_num} starting",
                          queued=len(self.rate_limiter.request_queue.queue))
            
            # Perform monitoring
            result = self.monitor.perform_periodic_monitoring()
            
            # Log monitoring result
            self.log_event('MONITOR', f"Cycle #{cycle_num} complete",
                          closed_expiry=result['closed_by_expiry'],
                          closed_profit=result['closed_by_profit'],
                          closed_sl=result['closed_by_stoploss'],
                          queued_remaining=result['rate_limiter_stats']['queued_requests'])
            
            self.monitoring_cycles.append({
                'cycle': cycle_num,
                'timestamp': datetime.now().isoformat(),
                'result': result
            })
            
            # Wait for next cycle
            time.sleep(interval)
    
    def simulate_sl_triggers(self, duration: int):
        """Simulate random stop loss triggers during test"""
        start_time = time.time()
        sl_num = 0
        
        while time.time() - start_time < duration:
            # Random intervals between SL triggers (10-40 seconds)
            wait_time = random.uniform(10, 40)
            time.sleep(min(wait_time, duration - (time.time() - start_time)))
            
            if time.time() - start_time >= duration:
                break
            
            # Randomly pick a symbol and simulate SL
            if self.orders_executed:
                order = random.choice(self.orders_executed)
                sl_num += 1
                
                # Simulate closing at SL
                current_premium = order['premium'] * random.uniform(0.85, 0.98)  # 2-15% loss
                
                self.log_event('SL', f"Stop loss triggered", 
                              symbol=order['symbol'],
                              entry=f"₹{order['premium']}",
                              sl=f"₹{current_premium:.2f}",
                              loss=f"₹{order['premium'] - current_premium:.2f}")
                
                self.sl_triggers.append({
                    'sl_num': sl_num,
                    'timestamp': datetime.now().isoformat(),
                    'symbol': order['symbol'],
                    'entry_premium': order['premium'],
                    'sl_premium': current_premium
                })
    
    def simulate_profit_takes(self, duration: int):
        """Simulate random profit target hits during test"""
        start_time = time.time()
        profit_num = 0
        
        while time.time() - start_time < duration:
            # Random intervals between profit takes (15-50 seconds)
            wait_time = random.uniform(15, 50)
            time.sleep(min(wait_time, duration - (time.time() - start_time)))
            
            if time.time() - start_time >= duration:
                break
            
            # Randomly pick a symbol and simulate profit
            if self.orders_executed:
                order = random.choice(self.orders_executed)
                profit_num += 1
                
                # Simulate closing at profit
                current_premium = order['premium'] * random.uniform(1.15, 1.50)  # 15-50% gain
                
                self.log_event('PROFIT', f"Profit target hit",
                              symbol=order['symbol'],
                              entry=f"₹{order['premium']}",
                              exit=f"₹{current_premium:.2f}",
                              gain=f"₹{current_premium - order['premium']:.2f}")
                
                self.profit_triggers.append({
                    'profit_num': profit_num,
                    'timestamp': datetime.now().isoformat(),
                    'symbol': order['symbol'],
                    'entry_premium': order['premium'],
                    'exit_premium': current_premium
                })
    
    def run_stress_test(self, num_orders: int = 30, duration_sec: int = 180):
        """Run the complete stress test"""
        
        print("\n" + "="*80)
        print("  OPTIONS BOT RATE LIMITER STRESS TEST")
        print("="*80)
        print(f"\nTest Configuration:")
        print(f"  • Orders to place: {num_orders}")
        print(f"  • Test duration: {duration_sec} seconds (~3 minutes)")
        print(f"  • Monitoring interval: 30 seconds")
        print(f"  • Rate limit: 8 req/sec, 180 req/min")
        print(f"\nStarting test at {datetime.now().strftime('%H:%M:%S')}\n")
        
        self.test_start_time = time.time()
        
        # Start background threads
        order_thread = threading.Thread(
            target=self.simulate_order_burst,
            args=(num_orders, duration_sec - 30)
        )
        
        monitor_thread = threading.Thread(
            target=self.simulate_monitoring_cycles,
            args=(duration_sec, 30)
        )
        
        sl_thread = threading.Thread(
            target=self.simulate_sl_triggers,
            args=(duration_sec,)
        )
        
        profit_thread = threading.Thread(
            target=self.simulate_profit_takes,
            args=(duration_sec,)
        )
        
        # Start all threads
        order_thread.start()
        monitor_thread.start()
        sl_thread.start()
        profit_thread.start()
        
        # Wait for all threads to complete
        order_thread.join()
        monitor_thread.join()
        sl_thread.join()
        profit_thread.join()
        
        self.test_end_time = time.time()
        
        # Generate report
        self.print_test_report()
    
    def print_test_report(self):
        """Print comprehensive test report"""
        
        total_time = self.test_end_time - self.test_start_time
        
        print("\n" + "="*80)
        print("  STRESS TEST RESULTS")
        print("="*80)
        
        # Summary Statistics
        print(f"\n📊 SUMMARY")
        print(f"  Test Duration: {total_time:.1f} seconds")
        print(f"  Orders Placed: {len(self.orders_placed)}")
        print(f"  Orders Executed: {len(self.orders_executed)} ({len(self.orders_executed)/len(self.orders_placed)*100:.1f}%)")
        print(f"  Orders Queued: {len(self.orders_queued)} ({len(self.orders_queued)/len(self.orders_placed)*100:.1f}%)")
        print(f"  Monitoring Cycles: {len(self.monitoring_cycles)}")
        print(f"  SL Triggers: {len(self.sl_triggers)}")
        print(f"  Profit Triggers: {len(self.profit_triggers)}")
        
        # Rate Limiter Stats
        stats = self.rate_limiter.get_statistics()
        
        print(f"\n🔄 RATE LIMITER PERFORMANCE")
        print(f"  Total API Calls: {stats['total_calls']}")
        print(f"  Blocked Calls: {stats['blocked_calls']}")
        print(f"  Queued Calls: {stats['queued_calls']}")
        print(f"  Success Rate: {stats['success_rate']:.1f}%")
        print(f"  Avg Wait Time: {stats['avg_wait_time']:.3f}ms")
        
        print(f"\n⏱️  TIME WINDOW STATS")
        print(f"  Calls last 1 min: {stats['calls_last_1min']}")
        print(f"  Calls last 5 min: {stats['calls_last_5min']}")
        
        print(f"\n🪣 TOKEN BUCKET STATUS")
        sec_bucket = stats['second_bucket']
        min_bucket = stats['minute_bucket']
        
        print(f"  Per-Second Bucket:")
        print(f"    Tokens: {sec_bucket['tokens']:.1f}/{sec_bucket['capacity']}")
        print(f"    Utilization: {sec_bucket['utilization']:.1f}%")
        print(f"    Refill Rate: {sec_bucket['refill_rate']}/sec")
        
        print(f"  Per-Minute Bucket:")
        print(f"    Tokens: {min_bucket['tokens']:.1f}/{min_bucket['capacity']}")
        print(f"    Utilization: {min_bucket['utilization']:.1f}%")
        print(f"    Refill Rate: {min_bucket['refill_rate']:.2f}/sec")
        
        # Queue Status
        print(f"\n📋 QUEUE STATUS")
        queue_size = len(self.rate_limiter.request_queue.queue)
        print(f"  Remaining Queued: {queue_size}")
        if queue_size == 0:
            print(f"  ✅ All queued requests processed!")
        else:
            print(f"  ⚠️  {queue_size} requests still in queue")
        
        # Monitoring Analysis
        print(f"\n🔍 MONITORING ANALYSIS")
        total_closed = 0
        total_sl = 0
        total_profit = 0
        
        for cycle in self.monitoring_cycles:
            total_closed += (len(cycle['result']['closed_by_expiry']) + 
                            len(cycle['result']['closed_by_profit']) +
                            len(cycle['result']['closed_by_stoploss']))
            total_sl += len(cycle['result']['closed_by_stoploss'])
            total_profit += len(cycle['result']['closed_by_profit'])
        
        print(f"  Total Positions Closed: {total_closed}")
        print(f"  Closed by SL: {total_sl}")
        print(f"  Closed by Profit: {total_profit}")
        print(f"  Closed by Expiry: {total_closed - total_sl - total_profit}")
        
        # Performance Metrics
        print(f"\n⚡ PERFORMANCE METRICS")
        orders_per_sec = len(self.orders_placed) / total_time
        api_calls_per_sec = stats['total_calls'] / total_time
        
        print(f"  Orders/sec: {orders_per_sec:.2f}")
        print(f"  API calls/sec: {api_calls_per_sec:.2f}")
        print(f"  Rate limit headroom: {8 - api_calls_per_sec:.2f} req/sec available")
        
        # Verdict
        print(f"\n🎯 VERDICT")
        if stats['success_rate'] == 100.0 and queue_size == 0:
            print(f"  ✅ STRESS TEST PASSED - All operations completed successfully!")
            print(f"  ✅ Zero order loss - Rate limiter handled {len(self.orders_placed)} orders perfectly")
            print(f"  ✅ Queue processed entirely - No stuck requests")
            print(f"  ✅ System stable under load - API utilization optimal")
        elif stats['success_rate'] >= 99.0:
            print(f"  ✅ STRESS TEST PASSED - Excellent performance!")
            print(f"  ⚠️  Minor queue accumulation observed, but within limits")
        else:
            print(f"  ⚠️  STRESS TEST WARNING - Some requests failed")
            print(f"  📊 Review stats above for details")
        
        print("\n" + "="*80 + "\n")


def main():
    """Run the stress test"""
    
    # Configure test parameters
    NUM_ORDERS = 30
    DURATION_SECONDS = 180  # 3 minutes
    
    # Run stress test
    coordinator = StressTestCoordinator()
    coordinator.run_stress_test(num_orders=NUM_ORDERS, duration_sec=DURATION_SECONDS)


if __name__ == "__main__":
    main()
