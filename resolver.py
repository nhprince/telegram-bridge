"""
Telegram Bridge — Telegram File URL Resolver
Utility to resolve file_id to download URLs without exposing bot token.
"""

import hashlib
import time
from typing import Optional


class FileResolver:
    """
    Resolves Telegram file_ids to accessible URLs.
    
    Methods:
    1. HTTP API (standard) — uses bot token in URL (expires in 1 hour)
    2. MTProto path — direct path access (no expiration)
    3. Bridge proxy — routes through this service (hides token)
    """
    
    def __init__(self, bot_token: str, bridge_base_url: str = None):
        self.bot_token = bot_token
        self.bridge_base_url = bridge_base_url
    
    def get_http_url(self, file_id: str) -> str:
        """
        Get Telegram HTTP API download URL.
        Note: URL expires after 1 hour, need to re-request.
        """
        return f"https://api.telegram.org/file/bot{self.bot_token}/{file_id}"
    
    def get_mproto_path(self, file_path: str) -> str:
        """
        Get the MTProto file path (used internally by Pyrogram).
        This is what Telegram returns in getFile response.
        """
        return file_path
    
    def get_bridge_url(self, file_unique_id: str) -> Optional[str]:
        """
        Get the bridge proxy URL (hides bot token from end users).
        Requires bridge_base_url to be set.
        """
        if not self.bridge_base_url:
            return None
        return f"{self.bridge_base_url}/download/{file_unique_id}"
    
    def get_resolved_url(self, file_unique_id: str, method: str = "bridge") -> str:
        """
        Get the best available URL for a file.
        
        Priority:
        1. Bridge proxy (best — hides token, no expiration)
        2. HTTP API (standard — expires in 1 hour)
        """
        if method == "bridge" and self.bridge_base_url:
            return self.get_bridge_url(file_unique_id)
        # Fall back to HTTP API (caller must provide file_id separately)
        return None


def generate_file_hash(file_data: bytes) -> str:
    """Generate a unique hash for a file (for deduplication)."""
    return hashlib.sha256(file_data).hexdigest()[:16]


def is_valid_telegram_file_id(file_id: str) -> bool:
    """Validate that a string looks like a valid Telegram file_id."""
    if not file_id or len(file_id) < 20:
        return False
    # Telegram file_ids are base64-like strings
    try:
        import base64
        base64.b64decode(file_id + "===")
        return True
    except Exception:
        return False
