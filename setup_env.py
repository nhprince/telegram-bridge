#!/usr/bin/env python3
"""Set up the .env file with actual credentials."""
import os

# These are the actual credentials
BOT_TOKEN = "8813852341:AAEfGEEF4UcbQd5uGpGM0o3K_5xxdsrsIcA"
API_ID = "33236458"
API_HASH = "a8078ce6bbfebe76b51e192fb8786d95"
CHANNEL_ID = "-1003924414897"
API_SECRET_KEY = "prince_snaps_bridge_2026_secure_key"

env_content = f"""TELEGRAM_API_ID={API_ID}
TELEGRAM_API_HASH={API_HASH}
BOT_TOKEN={BOT_TOKEN}
TELEGRAM_CHANNEL_ID={CHANNEL_ID}
BRIDGE_HOST=0.0.0.0
BRIDGE_PORT=9000
API_SECRET_KEY={API_SECRET_KEY}
ALLOWED_ORIGINS=*
DATABASE_PATH=bridge.db
MAX_FILE_SIZE=2147483648
"""

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
with open(env_path, "w") as f:
    f.write(env_content)

print(f"Written .env file ({len(env_content)} bytes)")
print(f"BOT_TOKEN length: {len(BOT_TOKEN)}")
print(f"API_HASH length: {len(API_HASH)}")
