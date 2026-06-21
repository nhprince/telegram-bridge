#!/bin/bash
# Start the Telegram Bridge
cd /home/nhprince/telegram-bridge
source venv/bin/activate

# Kill any existing gunicorn on port 9000
fuser -k 9000/tcp 2>/dev/null
sleep 2

# Ensure log files are writable
touch /tmp/bridge-access.log /tmp/bridge-error.log

# Start gunicorn
exec gunicorn main:app \
  -w 1 \
  -k uvicorn.workers.UvicornWorker \
  -b 127.0.0.1:9000 \
  --access-logfile /tmp/bridge-access.log \
  --error-logfile /tmp/bridge-error.log \
  --timeout 120
