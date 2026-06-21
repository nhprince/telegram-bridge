"""
Telegram Bridge — API Key Management
Simple per-app API key system stored in SQLite.
"""

import secrets
import time
from contextlib import contextmanager
from config import DATABASE_PATH
import sqlite3


def init_key_table():
    """Create api_keys table if not exists."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            app_id TEXT NOT NULL,
            name TEXT,
            is_active INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            last_used_at REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_key ON api_keys(key)")
    conn.commit()
    conn.close()


def generate_key(app_id: str, name: str = "") -> str:
    """Generate a new API key for an app. Returns the key (shown once)."""
    key = "tb_" + secrets.token_urlsafe(32)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        "INSERT INTO api_keys (key, app_id, name, created_at) VALUES (?, ?, ?, ?)",
        (key, app_id, name, time.time())
    )
    conn.commit()
    conn.close()
    return key


def validate_key(key: str) -> dict | None:
    """Validate an API key and return its metadata, or None if invalid."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM api_keys WHERE key = ? AND is_active = 1",
        (key,)
    ).fetchone()
    if row:
        # Update last_used_at
        conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key = ?",
            (time.time(), key)
        )
        conn.commit()
        conn.close()
        return dict(row)
    conn.close()
    return None


def revoke_key(key: str) -> bool:
    """Revoke (deactivate) an API key."""
    conn = sqlite3.connect(DATABASE_PATH)
    result = conn.execute(
        "UPDATE api_keys SET is_active = 0 WHERE key = ?",
        (key,)
    )
    conn.commit()
    conn.close()
    return result.rowcount > 0


def list_keys(app_id: str | None = None) -> list[dict]:
    """List all active API keys, optionally filtered by app_id."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    if app_id:
        rows = conn.execute(
            "SELECT key, app_id, name, created_at, last_used_at FROM api_keys WHERE app_id = ? AND is_active = 1",
            (app_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, app_id, name, created_at, last_used_at FROM api_keys WHERE is_active = 1"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
