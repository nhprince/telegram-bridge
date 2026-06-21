"""
Telegram Bridge — Cleanup Job
Removes soft-deleted files from DB after a configurable TTL.
"""

import logging
import time
from database import get_connection

logger = logging.getLogger(__name__)

# Default TTL: 30 days for soft-deleted files
DEFAULT_CLEANUP_TTL = 2592000  # 30 days in seconds


def cleanup_expired_files(ttl_seconds: int = DEFAULT_CLEANUP_TTL) -> dict:
    """
    Remove soft-deleted files older than the TTL from the database.
    Note: Files remain in Telegram (we can't delete from Telegram API),
    but they disappear from listings.
    
    Args:
        ttl_seconds: How long to keep soft-deleted files before hard-deleting from DB.
    
    Returns:
        dict with cleanup stats.
    """
    cutoff = time.time() - ttl_seconds

    with get_connection() as conn:
        # Find expired soft-deleted files
        rows = conn.execute(
            """SELECT id, file_unique_id, file_name, app_id, uploaded_at 
               FROM files 
               WHERE is_deleted = 1 AND uploaded_at < ?""",
            (cutoff,)
        ).fetchall()

        expired_count = len(rows)

        if expired_count > 0:
            # Hard delete expired records
            conn.execute(
                """DELETE FROM files 
                   WHERE is_deleted = 1 AND uploaded_at < ?""",
                (cutoff,)
            )

        # Also clean up empty folders
        conn.execute(
            """DELETE FROM folders 
               WHERE app_id NOT IN (SELECT DISTINCT app_id FROM files WHERE is_deleted = 0)
               AND app_id NOT IN (SELECT DISTINCT app_id FROM folders WHERE folder_id != 'root')"""
        )

        return {
            "expired_files_removed": expired_count,
            "ttl_seconds": ttl_seconds,
            "cutoff_timestamp": cutoff,
            "cleaned_at": time.time(),
        }


def get_expired_file_count(ttl_seconds: int = DEFAULT_CLEANUP_TTL) -> int:
    """Count how many soft-deleted files are past the TTL."""
    cutoff = time.time() - ttl_seconds
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as count FROM files WHERE is_deleted = 1 AND uploaded_at < ?",
            (cutoff,)
        ).fetchone()
        return row["count"] if row else 0
