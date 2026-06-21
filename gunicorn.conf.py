"""
Telegram Bridge — Production Gunicorn Configuration
"""
import multiprocessing
import os

# Server socket
bind = "127.0.0.1:9000"
backlog = 2048

# Worker processes — MUST be 1 because Pyrogram MTProto session is a single SQLite file
# that cannot be shared between multiple processes. Async handles concurrency.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
threads = 4

# Timeouts — 5400s (90 min) to support 500MB uploads at 0.15 MB/s
# 500MB / 0.15 MB/s = 3333s = 55 min + 35 min overhead = 90 min
timeout = 5400
graceful_timeout = 60
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
max_requests = 10000
max_requests_jitter = 1000

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
