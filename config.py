"""
Telegram Bridge — Configuration
All sensitive values are loaded from environment variables.
"""

import os
from dotenv import load_dotenv

# Load .env from the same directory as this file
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(env_path, override=True)

# Telegram API Credentials (from my.telegram.org)
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

# Storage Bot Token (from @BotFather)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Private Channel ID (negative number)
CHANNEL_ID = int(os.environ.get("TELEGRAM_CHANNEL_ID", "0"))

# Bridge Service Settings
BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "9000"))

# Upload Settings
# Max file size: 500 MB (at 0.15 MB/s upload speed, this takes ~55 minutes)
# Timeout is set to 5400s (90 min) to provide comfortable overhead
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
CHUNK_SIZE = 512 * 1024  # 512 KB chunks for MTProto uploads
UPLOAD_TIMEOUT = 5400  # 90 minutes in seconds

# Security
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "change-me-in-production")
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

# Per-app API keys (comma-separated: app_id:key,app_id:key)
PER_APP_KEYS = os.environ.get("PER_APP_KEYS", "").split(",") if os.environ.get("PER_APP_KEYS") else []

# Database (optional — for tracking uploads)
DATABASE_PATH = os.environ.get("DATABASE_PATH", "bridge.db")
