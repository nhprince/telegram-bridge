"""
Telegram Bridge — Per-App Storage Quotas
Tracks and enforces storage limits per application.
"""

import time
from database import get_connection
from config import DATABASE_PATH


def init_quotas_table():
    """Initialize the storage quotas table."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS storage_quotas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT UNIQUE NOT NULL,
                max_bytes INTEGER DEFAULT 1073741824,
                warn_at_percent INTEGER DEFAULT 80,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
        """)


def get_quota(app_id: str) -> dict:
    """Get quota info for an app, including current usage."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM storage_quotas WHERE app_id = ?",
            (app_id,)
        ).fetchone()

        if not row:
            # Default: 1GB quota
            return {
                "app_id": app_id,
                "max_bytes": 1073741824,
                "used_bytes": 0,
                "warn_at_percent": 80,
                "within_limit": True,
            }

        quota = dict(row)

        # Get current usage
        usage_row = conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) as used FROM files WHERE app_id = ? AND is_deleted = 0",
            (app_id,)
        ).fetchone()

        used = usage_row["used"] if usage_row else 0
        quota["used_bytes"] = used
        quota["within_limit"] = used < quota["max_bytes"]
        quota["percent_used"] = round((used / max(1, quota["max_bytes"])) * 100, 1)

        return quota


def set_quota(app_id: str, max_bytes: int, warn_at_percent: int = 80) -> dict:
    """Set or update storage quota for an app."""
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO storage_quotas (app_id, max_bytes, warn_at_percent, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(app_id) DO UPDATE SET
                   max_bytes = excluded.max_bytes,
                   warn_at_percent = excluded.warn_at_percent,
                   updated_at = excluded.updated_at""",
            (app_id, max_bytes, warn_at_percent, now, now)
        )
    return get_quota(app_id)


def check_quota(app_id: str, file_size: int) -> tuple[bool, dict]:
    """
    Check if uploading a file of given size would exceed the quota.
    
    Returns:
        (allowed: bool, quota_info: dict)
    """
    quota = get_quota(app_id)
    new_total = quota["used_bytes"] + file_size
    allowed = new_total <= quota["max_bytes"]
    return allowed, quota


def delete_quota(app_id: str) -> bool:
    """Remove quota entry for an app."""
    with get_connection() as conn:
        result = conn.execute(
            "DELETE FROM storage_quotas WHERE app_id = ?",
            (app_id,)
        )
        return result.rowcount > 0
