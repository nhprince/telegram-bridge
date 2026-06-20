"""
Telegram Bridge — Database module
Tracks uploaded files and their Telegram metadata.
"""

import sqlite3
import time
from contextlib import contextmanager
from config import DATABASE_PATH


def init_db():
    """Initialize the database schema."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_unique_id TEXT UNIQUE NOT NULL,
                file_id TEXT NOT NULL,
                file_name TEXT,
                file_size INTEGER,
                mime_type TEXT,
                channel_id INTEGER,
                uploaded_by TEXT,
                uploaded_at REAL,
                download_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_uploads_file_unique_id 
                ON uploads(file_unique_id);
            CREATE INDEX IF NOT EXISTS idx_uploads_uploaded_by 
                ON uploads(uploaded_by);
        """)


@contextmanager
def get_connection():
    """Get a database connection with automatic cleanup."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_upload(file_unique_id: str, file_id: str, file_name: str, 
               file_size: int, mime_type: str, channel_id: int, 
               uploaded_by: str = "prince-snaps"):
    """Save upload metadata to database."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO uploads 
               (file_unique_id, file_id, file_name, file_size, mime_type, 
                channel_id, uploaded_by, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_unique_id, file_id, file_name, file_size, mime_type,
             channel_id, uploaded_by, time.time())
        )


def get_upload(file_unique_id: str):
    """Get upload metadata by file_unique_id."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM uploads WHERE file_unique_id = ?",
            (file_unique_id,)
        ).fetchone()
        return dict(row) if row else None


def increment_download_count(file_unique_id: str):
    """Increment download counter for a file."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE uploads SET download_count = download_count + 1 WHERE file_unique_id = ?",
            (file_unique_id,)
        )


def get_stats():
    """Get overall upload statistics."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as total_uploads, COALESCE(SUM(file_size), 0) as total_size, "
            "COALESCE(SUM(download_count), 0) as total_downloads FROM uploads"
        ).fetchone()
        return dict(row) if row else {}
