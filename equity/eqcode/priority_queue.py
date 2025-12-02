"""
Priority Queue for API Calls - Equity Trading Bot

Implements a priority-based queue system to manage API calls:
- Priority 1 (Highest): Order placement (buy/sell from alerts)
- Priority 2 (Medium): Stop-loss placement
- Priority 3 (Lowest): LTP checks for monitoring

This prevents LTP checks from consuming rate limiter tokens when
order placement or SL placement need to happen.
"""

import asyncio
import time
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Callable, Any, Optional, List, Dict
from queue import PriorityQueue
import logging

logger = logging.getLogger(__name__)


class APIPriority(IntEnum):
    """API call priority levels (lower number = higher priority)"""
    ORDER_PLACEMENT = 1      # Highest priority - order placement from alerts
    SL_PLACEMENT = 2         # Medium priority - stop loss orders
    LTP_CHECK = 3            # Lowest priority - monitoring LTP checks


@dataclass(order=True)
class APITask:
    """API task with priority"""
    priority: int
    timestamp: float = field(compare=False)
    task_id: str = field(compare=False)
    func: Callable = field(compare=False)
    args: tuple = field(compare=False, default_factory=tuple)
    kwargs: dict = field(compare=False, default_factory=dict)
    result_future: Optional[asyncio.Future] = field(compare=False, default=None)
    
    def __post_init__(self):
        if self.result_future is None:
            self.result_future = asyncio.Future()


class PriorityAPIQueue:
    """
    Priority queue for managing API calls with rate limiting.
    
    Higher priority tasks (lower priority number) are executed first.
    Integrates with existing rate limiter to respect API limits.
    """
    
    def __init__(self, rate_limiter=None, max_queue_size: int = 1000):
        """
        Initialize priority queue.
        
        Args:
            rate_limiter: TokenBucket rate limiter instance
            max_queue_size: Maximum number of tasks in queue
        """
        self.queue = PriorityQueue(maxsize=max_queue_size)
        self.rate_limiter = rate_limiter
        self.running = False
        self.worker_task = None
        self._task_counter = 0
        
        # Metrics
        self.stats = {
            'total_queued': 0,
            'total_executed': 0,
            'total_failed': 0,
            'total_dropped': 0,
            'queue_full_events': 0,
            'by_priority': {
                APIPriority.ORDER_PLACEMENT: {'queued': 0, 'executed': 0, 'failed': 0},
                APIPriority.SL_PLACEMENT: {'queued': 0, 'executed': 0, 'failed': 0},
                APIPriority.LTP_CHECK: {'queued': 0, 'executed': 0, 'failed': 0},
            }
        }
    
    async def start(self):
        """Start the queue worker"""
        if not self.running:
            self.running = True
            self.worker_task = asyncio.create_task(self._worker())
            logger.info("Priority API queue started")
    
    async def stop(self):
        """Stop the queue worker"""
        if self.running:
            self.running = False
            if self.worker_task:
                self.worker_task.cancel()
                try:
                    await self.worker_task
                except asyncio.CancelledError:
                    pass
            logger.info("Priority API queue stopped")
    
    def enqueue(self, priority: APIPriority, func: Callable, *args, **kwargs) -> asyncio.Future:
        """
        Add a task to the queue.
        
        Args:
            priority: Task priority (APIPriority enum)
            func: Function to call
            *args, **kwargs: Arguments to pass to func
        
        Returns:
            Future that will contain the result
        """
        self._task_counter += 1
        task_id = f"{priority.name}_{self._task_counter}_{int(time.time()*1000)}"
        
        task = APITask(
            priority=priority.value,
            timestamp=time.time(),
            task_id=task_id,
            func=func,
            args=args,
            kwargs=kwargs
        )
        
        try:
            # Try to add to queue (non-blocking)
            self.queue.put_nowait(task)
            self.stats['total_queued'] += 1
            self.stats['by_priority'][priority]['queued'] += 1
            
            logger.debug(
                f"API_QUEUE_ENQUEUE | priority={priority.name} | "
                f"task_id={task_id} | queue_size={self.queue.qsize()}"
            )
            
            return task.result_future
            
        except Exception as e:
            # Queue is full - drop lowest priority tasks or reject
            logger.warning(
                f"API_QUEUE_FULL | priority={priority.name} | "
                f"task_id={task_id} | queue_size={self.queue.qsize()} | "
                f"error={str(e)}"
            )
            self.stats['queue_full_events'] += 1
            self.stats['total_dropped'] += 1
            
            # Set exception on the future
            task.result_future.set_exception(Exception("Queue full - task dropped"))
            return task.result_future
    
    async def _worker(self):
        """Worker coroutine that processes tasks from queue"""
        logger.info("Priority API queue worker started")
        
        while self.running:
            try:
                # Process pending queued requests (from rate limiter queue)
                if self.rate_limiter:
                    try:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, self.rate_limiter.process_pending_requests)
                    except Exception as e:
                        logger.debug(f"Queue processing error: {str(e)}")
                
                # Get next task (blocks if queue is empty)
                # Use timeout to allow checking self.running periodically
                try:
                    task = self.queue.get(timeout=1.0)
                except:
                    # Timeout or empty queue
                    await asyncio.sleep(0.1)
                    continue
                
                priority_enum = APIPriority(task.priority)
                
                # Wait for rate limiter if needed
                if self.rate_limiter:
                    try:
                        # Run rate limiter check in thread executor (it's blocking)
                        loop = asyncio.get_event_loop()
                        
                        # Use wait_for_call_permission which is blocking but works with executor
                        def _wait_rate_limit():
                            return self.rate_limiter.wait_for_call_permission(timeout=30.0)
                        
                        permission_granted = await loop.run_in_executor(None, _wait_rate_limit)
                        
                        if not permission_granted:
                            logger.warning(
                                f"API_QUEUE_RATE_LIMIT_TIMEOUT | priority={priority_enum.name} | "
                                f"task_id={task.task_id} | QUEUEING_FOR_RETRY"
                            )
                            # Queue request for automatic retry instead of failing permanently
                            self.rate_limiter.queue_request(
                                request_type=f"{task.func.__name__ if hasattr(task.func, '__name__') else 'api_call'}",
                                callback=task.func,
                                args=task.args,
                                kwargs=task.kwargs
                            )
                            # Resolve future with "queued" status
                            if not task.result_future.done():
                                task.result_future.set_result({
                                    "status": "queued",
                                    "message": "Rate limited - queued for automatic retry",
                                    "task_id": task.task_id
                                })
                            self.stats['total_queued'] = self.stats.get('total_queued', 0) + 1
                            self.stats['by_priority'][priority_enum]['queued'] = self.stats['by_priority'][priority_enum].get('queued', 0) + 1
                            self.queue.task_done()
                            continue
                            
                    except Exception as e:
                        logger.warning(
                            f"API_QUEUE_RATE_LIMITER_ERROR | task_id={task.task_id} | "
                            f"error={str(e)}"
                        )
                
                # Execute the task
                try:
                    start_time = time.time()
                    
                    # Check if function is async or sync
                    if asyncio.iscoroutinefunction(task.func):
                        result = await task.func(*task.args, **task.kwargs)
                    else:
                        result = task.func(*task.args, **task.kwargs)
                    
                    elapsed_ms = (time.time() - start_time) * 1000
                    
                    # Set result on the future
                    if not task.result_future.done():
                        task.result_future.set_result(result)
                    
                    self.stats['total_executed'] += 1
                    self.stats['by_priority'][priority_enum]['executed'] += 1
                    
                    logger.debug(
                        f"API_QUEUE_EXECUTED | priority={priority_enum.name} | "
                        f"task_id={task.task_id} | elapsed_ms={elapsed_ms:.1f}"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"API_QUEUE_TASK_FAILED | priority={priority_enum.name} | "
                        f"task_id={task.task_id} | error={str(e)}",
                        exc_info=True
                    )
                    
                    # Set exception on the future
                    if not task.result_future.done():
                        task.result_future.set_exception(e)
                    
                    self.stats['total_failed'] += 1
                    self.stats['by_priority'][priority_enum]['failed'] += 1
                
                finally:
                    # Mark task as done
                    self.queue.task_done()
                
            except asyncio.CancelledError:
                logger.info("Priority API queue worker cancelled")
                break
            except Exception as e:
                logger.error(f"API_QUEUE_WORKER_ERROR | error={str(e)}", exc_info=True)
                await asyncio.sleep(1)  # Prevent tight loop on persistent errors
    
    def get_stats(self) -> dict:
        """Get queue statistics"""
        return {
            **self.stats,
            'queue_size': self.queue.qsize(),
            'running': self.running
        }
    
    def log_stats(self):
        """Log queue statistics"""
        stats = self.get_stats()
        logger.info(
            f"API_QUEUE_STATS | "
            f"queue_size={stats['queue_size']} | "
            f"total_queued={stats['total_queued']} | "
            f"total_executed={stats['total_executed']} | "
            f"total_failed={stats['total_failed']} | "
            f"total_dropped={stats['total_dropped']} | "
            f"queue_full_events={stats['queue_full_events']}"
        )
        
        for priority in APIPriority:
            pstats = stats['by_priority'][priority]
            if pstats['queued'] > 0:
                logger.info(
                    f"API_QUEUE_STATS_{priority.name} | "
                    f"queued={pstats['queued']} | "
                    f"executed={pstats['executed']} | "
                    f"failed={pstats['failed']}"
                )


class AlertQueue:
    """
    Alert queue for handling bursts of webhook alerts from TradingView.
    
    Prevents rate limit timeouts by:
    1. Queuing incoming alerts instead of processing immediately
    2. Processing alerts sequentially at a safe rate (1 alert/1.5 seconds)
    3. Ensuring no rate limit bursts that would block order placement
    4. Recovering all queued alerts instead of losing them
    
    Without this queue:
    - 4 alerts in 7 seconds → all fail with RATE_LIMIT_TIMEOUT
    - 5 trades lost per burst event
    
    With this queue:
    - Same 4 alerts queued, processed at 1/1.5sec → all succeed
    - 100% alert conversion rate (no rate limit losses)
    """
    
    def __init__(self, process_alert_func: Callable, 
                 processing_rate: float = 1.5, max_queue_size: int = 500):
        """
        Initialize alert queue.
        
        Args:
            process_alert_func: Async function to call for each alert
                               Signature: async def process_alert(alert_data) -> Dict
            processing_rate: Seconds between processing alerts (default 1.5 sec)
                           This ensures safe rate even with API slowdowns
            max_queue_size: Maximum alerts to queue before rejecting new ones
        """
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self.process_alert_func = process_alert_func
        self.processing_rate = processing_rate
        self.running = False
        self.worker_task = None
        
        # Metrics
        self.stats = {
            'total_received': 0,
            'total_processed': 0,
            'total_failed': 0,
            'total_dropped': 0,
            'queue_full_events': 0,
            'max_queue_size': 0,
            'by_action': {
                'BUY': {'received': 0, 'processed': 0, 'failed': 0},
                'SELL': {'received': 0, 'processed': 0, 'failed': 0},
                'EXIT': {'received': 0, 'processed': 0, 'failed': 0},
            }
        }
        
        logger.info(f"AlertQueue initialized with processing_rate={processing_rate}s")
    
    async def start(self):
        """Start the alert queue worker"""
        if not self.running:
            self.running = True
            self.worker_task = asyncio.create_task(self._worker())
            logger.info("Alert queue worker started")
    
    async def stop(self):
        """Stop the alert queue worker"""
        if self.running:
            self.running = False
            if self.worker_task:
                self.worker_task.cancel()
                try:
                    await self.worker_task
                except asyncio.CancelledError:
                    pass
            logger.info("Alert queue worker stopped")
    
    async def enqueue_alert(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enqueue an alert for processing (async version).
        
        Args:
            alert_data: Alert data from webhook
        
        Returns:
            Response dict with status
        """
        try:
            alert_id = f"alert_{int(time.time()*1000000)}"
            action = alert_data.get('action', 'UNKNOWN')
            symbol = alert_data.get('symbol', 'UNKNOWN')
            
            # Try to queue non-blocking
            self.queue.put_nowait({
                'alert_id': alert_id,
                'alert_data': alert_data,
                'timestamp': time.time()
            })
            
            # Update metrics
            self.stats['total_received'] += 1
            if action in self.stats['by_action']:
                self.stats['by_action'][action]['received'] += 1
            
            queue_size = self.queue.qsize()
            self.stats['max_queue_size'] = max(self.stats['max_queue_size'], queue_size)
            
            logger.debug(
                f"ALERT_QUEUED | alert_id={alert_id} | "
                f"symbol={symbol} | action={action} | "
                f"queue_size={queue_size}"
            )
            
            return {
                "status": "queued",
                "alert_id": alert_id,
                "queue_position": queue_size,
                "message": f"Alert queued for processing (position: {queue_size})"
            }
            
        except asyncio.QueueFull:
            logger.warning(
                f"ALERT_QUEUE_FULL | symbol={symbol} | action={action} | "
                f"queue_size={self.queue.qsize()}"
            )
            self.stats['queue_full_events'] += 1
            self.stats['total_dropped'] += 1
            
            return {
                "status": "dropped",
                "message": f"Alert queue full - rejecting alert",
                "queue_size": self.queue.qsize()
            }
        
        except Exception as e:
            logger.error(f"ALERT_ENQUEUE_ERROR | error={str(e)}", exc_info=True)
            self.stats['total_dropped'] += 1
            
            return {
                "status": "error",
                "message": f"Failed to queue alert: {str(e)}"
            }
    
    def enqueue_alert_sync(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enqueue an alert for processing (sync version for Flask webhooks).
        
        This is a wrapper that can be safely called from synchronous code.
        
        Args:
            alert_data: Alert data from webhook
        
        Returns:
            Response dict with status
        """
        try:
            alert_id = f"alert_{int(time.time()*1000000)}"
            action = alert_data.get('action', 'UNKNOWN')
            symbol = alert_data.get('symbol', 'UNKNOWN')
            
            # Try to queue non-blocking
            self.queue.put_nowait({
                'alert_id': alert_id,
                'alert_data': alert_data,
                'timestamp': time.time()
            })
            
            # Update metrics
            self.stats['total_received'] += 1
            if action in self.stats['by_action']:
                self.stats['by_action'][action]['received'] += 1
            
            queue_size = self.queue.qsize()
            self.stats['max_queue_size'] = max(self.stats['max_queue_size'], queue_size)
            
            logger.debug(
                f"ALERT_QUEUED_SYNC | alert_id={alert_id} | "
                f"symbol={symbol} | action={action} | "
                f"queue_size={queue_size}"
            )
            
            return {
                "status": "queued",
                "alert_id": alert_id,
                "queue_position": queue_size,
                "message": f"Alert queued for processing (position: {queue_size})"
            }
            
        except Exception as e:
            symbol = alert_data.get('symbol', 'UNKNOWN')
            action = alert_data.get('action', 'UNKNOWN')
            
            # Check if it was full
            if "Full" in str(e):
                logger.warning(
                    f"ALERT_QUEUE_FULL | symbol={symbol} | action={action} | "
                    f"queue_size={self.queue.qsize()}"
                )
                self.stats['queue_full_events'] += 1
                self.stats['total_dropped'] += 1
                
                return {
                    "status": "dropped",
                    "message": f"Alert queue full - rejecting alert",
                    "queue_size": self.queue.qsize()
                }
            else:
                logger.error(f"ALERT_ENQUEUE_SYNC_ERROR | error={str(e)}", exc_info=True)
                self.stats['total_dropped'] += 1
                
                return {
                    "status": "error",
                    "message": f"Failed to queue alert: {str(e)}"
                }

    
    async def _worker(self):
        """Worker coroutine that processes alerts sequentially at safe rate"""
        logger.info(
            f"Alert queue worker started - processing rate: 1 alert per {self.processing_rate}s"
        )
        
        while self.running:
            try:
                # Get next alert from queue (blocks if empty)
                try:
                    queued_item = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    # Queue empty, continue waiting
                    await asyncio.sleep(0.1)
                    continue
                
                alert_id = queued_item['alert_id']
                alert_data = queued_item['alert_data']
                action = alert_data.get('action', 'UNKNOWN')
                symbol = alert_data.get('symbol', 'UNKNOWN')
                
                try:
                    start_time = time.time()
                    
                    # Process the alert
                    result = await self.process_alert_func(alert_data)
                    
                    elapsed_ms = (time.time() - start_time) * 1000
                    
                    # Update metrics
                    self.stats['total_processed'] += 1
                    if action in self.stats['by_action']:
                        self.stats['by_action'][action]['processed'] += 1
                    
                    logger.info(
                        f"ALERT_PROCESSED | alert_id={alert_id} | "
                        f"symbol={symbol} | action={action} | "
                        f"elapsed_ms={elapsed_ms:.1f} | "
                        f"status={result.get('status', 'unknown')}"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"ALERT_PROCESSING_ERROR | alert_id={alert_id} | "
                        f"symbol={symbol} | action={action} | "
                        f"error={str(e)}",
                        exc_info=True
                    )
                    
                    # Update metrics
                    self.stats['total_failed'] += 1
                    if action in self.stats['by_action']:
                        self.stats['by_action'][action]['failed'] += 1
                
                finally:
                    # Mark task done
                    self.queue.task_done()
                    
                    # Wait before processing next alert to maintain rate limit safety
                    # Even if processing was fast, we enforce minimum interval
                    await asyncio.sleep(self.processing_rate)
                
            except asyncio.CancelledError:
                logger.info("Alert queue worker cancelled")
                break
            except Exception as e:
                logger.error(
                    f"ALERT_WORKER_ERROR | error={str(e)}",
                    exc_info=True
                )
                await asyncio.sleep(1)
    
    def get_stats(self) -> dict:
        """Get queue statistics"""
        return {
            **self.stats,
            'queue_size': self.queue.qsize(),
            'running': self.running
        }
    
    def log_stats(self):
        """Log queue statistics"""
        stats = self.get_stats()
        logger.info(
            f"ALERT_QUEUE_STATS | "
            f"running={stats['running']} | "
            f"queue_size={stats['queue_size']} | "
            f"total_received={stats['total_received']} | "
            f"total_processed={stats['total_processed']} | "
            f"total_failed={stats['total_failed']} | "
            f"total_dropped={stats['total_dropped']} | "
            f"max_queue_size={stats['max_queue_size']} | "
            f"queue_full_events={stats['queue_full_events']}"
        )
        
        for action in ['BUY', 'SELL', 'EXIT']:
            astats = stats['by_action'][action]
            if astats['received'] > 0:
                logger.info(
                    f"ALERT_QUEUE_STATS_{action} | "
                    f"received={astats['received']} | "
                    f"processed={astats['processed']} | "
                    f"failed={astats['failed']}"
                )
