"""
Telegram Bridge — Production Gunicorn Configuration
"""
import multiprocessing
import os

# Server socket
bind = "127.0.0.1:9000"  # Internal only — exposed via nginx/cf tunnel
backlog = 2048

# Worker processes
# For async FastAPI: use 1 worker with uvicorn (handles thousands of concurrent connections via async)
# For high-CPU workloads: use (2 × num_cores) + 1 workers with threads
workers = 1  # Single async worker handles thousands of concurrent requests
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
threads = 4  # Fallback for sync operations

# Timeouts
timeout = 120  # 2 min max per request (uploads can take time)
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "/var/log/telegram-bridge/access.log"
errorlog = "/var/log/telegram-bridge/error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "telegram-bridge"

# Server mechanics
preload_app = True
daemon = False
tmp_upload_dir = "/tmp/telegram-bridge-uploads"

# SSL (handled by nginx/cf tunnel, not here)
forwarded_allow_ips = "*"
secure_scheme_headers = {"X-Forwarded-Proto": "https"}

# Performance
max_requests = 10000  # Restart workers after 10K requests (prevents memory leaks)
max_requests_jitter = 1000  # Add randomness to prevent all workers restarting at once

# Graceful restart
restart = False

def on_starting(server):
    """Called when the server starts."""
    pass

def post_fork(server, worker):
    """Called after a worker is forked."""
    server.log.info(f"Worker spawned (pid: {worker.pid})")

def pre_exec(server):
    """Called before a new master process is forked."""
    server.log.info("Forked child, re-executing.")
