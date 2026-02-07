"""
Comprehensive monitoring and observability module for T1API
Provides metrics, tracing, error tracking, and load monitoring
"""
import time
import psutil
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict, deque
from threading import Lock
from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import uuid

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

# Request metrics
REQUEST_COUNT = Counter(
    'api_requests_total',
    'Total number of API requests',
    ['method', 'endpoint', 'status', 'tier']
)

REQUEST_DURATION = Histogram(
    'api_request_duration_seconds',
    'Request duration in seconds',
    ['method', 'endpoint', 'tier'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

REQUEST_SIZE = Histogram(
    'api_request_size_bytes',
    'Request size in bytes',
    ['method', 'endpoint']
)

RESPONSE_SIZE = Histogram(
    'api_response_size_bytes',
    'Response size in bytes',
    ['method', 'endpoint', 'status']
)

# Error metrics
ERROR_COUNT = Counter(
    'api_errors_total',
    'Total number of errors',
    ['endpoint', 'error_type', 'status_code']
)

EXCEPTION_COUNT = Counter(
    'api_exceptions_total',
    'Total number of unhandled exceptions',
    ['endpoint', 'exception_type']
)

# System metrics
ACTIVE_REQUESTS = Gauge(
    'api_active_requests',
    'Number of requests currently being processed'
)

CPU_USAGE = Gauge(
    'api_cpu_usage_percent',
    'Current CPU usage percentage'
)

MEMORY_USAGE = Gauge(
    'api_memory_usage_bytes',
    'Current memory usage in bytes'
)

MEMORY_PERCENT = Gauge(
    'api_memory_usage_percent',
    'Current memory usage percentage'
)

# Rate limit metrics
RATE_LIMIT_HITS = Counter(
    'api_rate_limit_hits_total',
    'Number of requests that hit rate limits',
    ['tier', 'endpoint']
)

# Cache metrics (if using cache)
CACHE_HITS = Counter(
    'api_cache_hits_total',
    'Number of cache hits',
    ['endpoint']
)

CACHE_MISSES = Counter(
    'api_cache_misses_total',
    'Number of cache misses',
    ['endpoint']
)

# Background processor metrics
BACKGROUND_JOBS_PROCESSED = Counter(
    'api_background_jobs_processed_total',
    'Total number of background jobs processed'
)

BACKGROUND_JOBS_FAILED = Counter(
    'api_background_jobs_failed_total',
    'Total number of failed background jobs'
)

# API Info
API_INFO = Info('api_info', 'API version and environment information')


# ============================================================================
# REQUEST TRACKER - In-Memory Detailed Request History
# ============================================================================

class RequestTracker:
    """
    Track detailed information about all requests for full traceability
    Keeps recent requests in memory with complete details
    """
    
    def __init__(self, max_stored_requests: int = 1000):
        self.max_stored_requests = max_stored_requests
        self.requests = deque(maxlen=max_stored_requests)
        self.errors = deque(maxlen=500)  # Keep last 500 errors
        self.lock = Lock()
        
        # Statistics
        self.total_requests = 0
        self.total_errors = 0
        self.endpoint_stats = defaultdict(lambda: {
            'count': 0,
            'errors': 0,
            'total_duration': 0.0,
            'min_duration': float('inf'),
            'max_duration': 0.0
        })
        
        logger.info("Request tracker initialized")
    
    def track_request(self, request_data: Dict):
        """Track a completed request"""
        with self.lock:
            self.requests.append(request_data)
            self.total_requests += 1
            
            # Update endpoint statistics
            endpoint = request_data.get('endpoint', 'unknown')
            duration = request_data.get('duration', 0)
            status = request_data.get('status_code', 0)
            
            stats = self.endpoint_stats[endpoint]
            stats['count'] += 1
            stats['total_duration'] += duration
            stats['min_duration'] = min(stats['min_duration'], duration)
            stats['max_duration'] = max(stats['max_duration'], duration)
            
            if status >= 400:
                stats['errors'] += 1
                self.total_errors += 1
    
    def track_error(self, error_data: Dict):
        """Track an error with full details"""
        with self.lock:
            self.errors.append(error_data)
    
    def get_recent_requests(self, limit: int = 100) -> List[Dict]:
        """Get recent requests"""
        with self.lock:
            return list(self.requests)[-limit:]
    
    def get_recent_errors(self, limit: int = 50) -> List[Dict]:
        """Get recent errors"""
        with self.lock:
            return list(self.errors)[-limit:]
    
    def get_endpoint_stats(self) -> Dict:
        """Get statistics per endpoint"""
        with self.lock:
            result = {}
            for endpoint, stats in self.endpoint_stats.items():
                avg_duration = stats['total_duration'] / stats['count'] if stats['count'] > 0 else 0
                error_rate = stats['errors'] / stats['count'] if stats['count'] > 0 else 0
                
                result[endpoint] = {
                    'total_requests': stats['count'],
                    'total_errors': stats['errors'],
                    'error_rate': round(error_rate * 100, 2),
                    'avg_duration_ms': round(avg_duration * 1000, 2),
                    'min_duration_ms': round(stats['min_duration'] * 1000, 2) if stats['min_duration'] != float('inf') else 0,
                    'max_duration_ms': round(stats['max_duration'] * 1000, 2)
                }
            return result
    
    def get_summary(self) -> Dict:
        """Get overall summary statistics"""
        with self.lock:
            error_rate = (self.total_errors / self.total_requests * 100) if self.total_requests > 0 else 0
            return {
                'total_requests': self.total_requests,
                'total_errors': self.total_errors,
                'error_rate_percent': round(error_rate, 2),
                'tracked_requests_in_memory': len(self.requests),
                'tracked_errors_in_memory': len(self.errors),
                'unique_endpoints': len(self.endpoint_stats)
            }


# Global request tracker instance
_request_tracker = None

def get_request_tracker() -> RequestTracker:
    """Get or create the global request tracker"""
    global _request_tracker
    if _request_tracker is None:
        _request_tracker = RequestTracker()
    return _request_tracker


# ============================================================================
# SYSTEM MONITOR - Real-time System Metrics
# ============================================================================

class SystemMonitor:
    """Monitor system resources and performance"""
    
    def __init__(self):
        self.process = psutil.Process()
        self.start_time = datetime.utcnow()
        logger.info("System monitor initialized")
    
    def get_metrics(self) -> Dict:
        """Get current system metrics"""
        try:
            # CPU metrics
            cpu_percent = self.process.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count()
            system_cpu = psutil.cpu_percent(interval=0.1, percpu=False)
            
            # Memory metrics
            memory_info = self.process.memory_info()
            memory_percent = self.process.memory_percent()
            system_memory = psutil.virtual_memory()
            
            # Disk metrics
            disk = psutil.disk_usage('/')
            
            # Network metrics (if available)
            try:
                net_io = psutil.net_io_counters()
                network_info = {
                    'bytes_sent': net_io.bytes_sent,
                    'bytes_received': net_io.bytes_recv,
                    'packets_sent': net_io.packets_sent,
                    'packets_received': net_io.packets_recv,
                }
            except Exception:
                network_info = None
            
            # Update Prometheus gauges
            CPU_USAGE.set(cpu_percent)
            MEMORY_USAGE.set(memory_info.rss)
            MEMORY_PERCENT.set(memory_percent)
            
            uptime = datetime.utcnow() - self.start_time
            
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'uptime_seconds': int(uptime.total_seconds()),
                'uptime_human': str(uptime).split('.')[0],
                'cpu': {
                    'process_percent': round(cpu_percent, 2),
                    'system_percent': round(system_cpu, 2),
                    'count': cpu_count
                },
                'memory': {
                    'process_mb': round(memory_info.rss / 1024 / 1024, 2),
                    'process_percent': round(memory_percent, 2),
                    'system_total_mb': round(system_memory.total / 1024 / 1024, 2),
                    'system_available_mb': round(system_memory.available / 1024 / 1024, 2),
                    'system_percent': system_memory.percent
                },
                'disk': {
                    'total_gb': round(disk.total / 1024 / 1024 / 1024, 2),
                    'used_gb': round(disk.used / 1024 / 1024 / 1024, 2),
                    'free_gb': round(disk.free / 1024 / 1024 / 1024, 2),
                    'percent': disk.percent
                },
                'network': network_info
            }
        except Exception as e:
            logger.error(f"Error getting system metrics: {e}")
            return {'error': str(e)}


# Global system monitor instance
_system_monitor = None

def get_system_monitor() -> SystemMonitor:
    """Get or create the global system monitor"""
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitor()
    return _system_monitor


# ============================================================================
# REQUEST TRACING MIDDLEWARE
# ============================================================================

class RequestTracingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds comprehensive request tracing and metrics
    - Assigns unique request ID to each request
    - Tracks timing and performance
    - Records all requests with full details
    - Updates Prometheus metrics
    """
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.tracker = get_request_tracker()
        logger.info("Request tracing middleware initialized")
    
    async def dispatch(self, request: Request, call_next):
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Start timing
        start_time = time.time()
        
        # Increment active requests
        ACTIVE_REQUESTS.inc()
        
        # Extract request details
        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")
        api_key = request.headers.get("x-api-key", None)
        
        # Determine API tier
        tier = "unknown"
        if api_key:
            from src.utils.config import settings
            if api_key in settings.premium_api_keys_list:
                tier = "premium"
            elif api_key in settings.allowed_api_keys_list:
                tier = "standard"
            else:
                tier = "invalid"
        else:
            tier = "public"
        
        # Process request
        response = None
        status_code = 500
        error = None
        
        try:
            response = await call_next(request)
            status_code = response.status_code
            
            # Track request size
            if hasattr(request, 'body'):
                try:
                    body = await request.body()
                    REQUEST_SIZE.labels(method=method, endpoint=path).observe(len(body))
                except:
                    pass
            
            return response
            
        except Exception as exc:
            # Track exception
            error = {
                'type': type(exc).__name__,
                'message': str(exc),
                'traceback': traceback.format_exc()
            }
            
            EXCEPTION_COUNT.labels(
                endpoint=path,
                exception_type=type(exc).__name__
            ).inc()
            
            # Re-raise to let FastAPI handle it
            raise
            
        finally:
            # Calculate duration
            duration = time.time() - start_time
            
            # Decrement active requests
            ACTIVE_REQUESTS.dec()
            
            # Update metrics
            REQUEST_COUNT.labels(
                method=method,
                endpoint=path,
                status=status_code,
                tier=tier
            ).inc()
            
            REQUEST_DURATION.labels(
                method=method,
                endpoint=path,
                tier=tier
            ).observe(duration)
            
            if status_code >= 400:
                ERROR_COUNT.labels(
                    endpoint=path,
                    error_type='client_error' if status_code < 500 else 'server_error',
                    status_code=status_code
                ).inc()
            
            # Track response size
            if response and hasattr(response, 'body'):
                try:
                    RESPONSE_SIZE.labels(
                        method=method,
                        endpoint=path,
                        status=status_code
                    ).observe(len(response.body))
                except:
                    pass
            
            # Build detailed request record
            request_data = {
                'request_id': request_id,
                'timestamp': datetime.utcnow().isoformat(),
                'method': method,
                'endpoint': path,
                'full_url': str(request.url),
                'client_ip': client_ip,
                'user_agent': user_agent,
                'tier': tier,
                'status_code': status_code,
                'duration': duration,
                'duration_ms': round(duration * 1000, 2),
                'error': error
            }
            
            # Track in request tracker
            self.tracker.track_request(request_data)
            
            # If error, also track in error list
            if error or status_code >= 400:
                error_data = {
                    **request_data,
                    'error_details': error
                }
                self.tracker.track_error(error_data)
            
            # Log request
            log_message = f"[{request_id}] {method} {path} - {status_code} - {duration*1000:.2f}ms - {tier}"
            if status_code >= 500:
                logger.error(log_message)
            elif status_code >= 400:
                logger.warning(log_message)
            else:
                logger.info(log_message)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_prometheus_metrics() -> str:
    """Get Prometheus metrics in text format"""
    return generate_latest()

def get_prometheus_content_type() -> str:
    """Get the content type for Prometheus metrics"""
    return CONTENT_TYPE_LATEST


def set_api_info(name: str, version: str, environment: str):
    """Set API information for Prometheus"""
    API_INFO.info({
        'name': name,
        'version': version,
        'environment': environment
    })
