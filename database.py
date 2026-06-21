"""
Telegram Bridge — Database module (v2 — Universal Storage API)
Tracks uploaded files with folder organization, per-app isolation, and soft delete.
"""

import sqlite3
import time
from contextlib import contextmanager
from config import DATABASE_PATH


def init_db():
    """Initialize the database schema (idempotent — safe to run on existing DB)."""
    with get_connection() as conn:
        # ── Files table (extended in v2) ──────────────────────────────
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_unique_id TEXT UNIQUE NOT NULL,
                file_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mime_type TEXT,
                folder_id TEXT DEFAULT 'root',
                app_id TEXT NOT NULL,
                description TEXT,
                uploaded_by TEXT,
                uploaded_at REAL NOT NULL,
                download_count INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                parent_id TEXT DEFAULT 'root',
                app_id TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT UNIQUE NOT NULL,
                name TEXT,
                description TEXT,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_files_app_id 
                ON files(app_id);
            CREATE INDEX IF NOT EXISTS idx_files_folder 
                ON files(app_id, folder_id);
            CREATE INDEX IF NOT EXISTS idx_files_uploaded_at 
                ON files(app_id, uploaded_at);
            CREATE INDEX IF NOT EXISTS idx_files_is_deleted 
                ON files(app_id, is_deleted);
            CREATE INDEX IF NOT EXISTS idx_folders_app_id 
                ON folders(app_id);
        """)

        # ── Migration: add new columns if missing (existing v1 DB) ─────
        try:
            conn.execute("ALTER TABLE files ADD COLUMN folder_id TEXT DEFAULT 'root'")
        except sqlite3.OperationalError:
            pass  # column already exists

        try:
            conn.execute("ALTER TABLE files ADD COLUMN description TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE files ADD COLUMN is_deleted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # ── v2.2: add access_hash + file_reference for MTProto downloads ──
        try:
            conn.execute("ALTER TABLE files ADD COLUMN media_id INTEGER")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE files ADD COLUMN access_hash INTEGER")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE files ADD COLUMN file_reference BLOB")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE files ADD COLUMN dc_id INTEGER")
        except sqlite3.OperationalError:
            pass

        # ── Rename uploads → files if migrating from v1 ────────────────
        try:
            conn.execute("ALTER TABLE uploads RENAME TO files")
        except sqlite3.OperationalError:
            pass  # already renamed or doesn't exist


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


# ── File Operations ──────────────────────────────────────────────────

def save_upload(file_unique_id: str, file_id: str, file_name: str,
               file_size: int, mime_type: str, app_id: str,
               folder_id: str = "root", description: str | None = None,
               media_id: int = 0, access_hash: int = 0, file_reference: bytes = b"", dc_id: int = 0):
    """Save upload metadata to database."""
    with get_connection() as conn:
        # Register app if not exists
        conn.execute(
            "INSERT OR IGNORE INTO apps (app_id, name, created_at) VALUES (?, ?, ?)",
            (app_id, app_id, time.time())
        )

        conn.execute(
            """INSERT OR REPLACE INTO files 
               (file_unique_id, file_id, file_name, file_size, mime_type, 
                folder_id, app_id, description, uploaded_by, uploaded_at, is_deleted,
                media_id, access_hash, file_reference, dc_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
            (file_unique_id, file_id, file_name, file_size, mime_type,
             folder_id, app_id, description, app_id, time.time(),
             media_id, access_hash, file_reference, dc_id)
        )


def get_upload(file_unique_id: str, app_id: str | None = None):
    """Get upload metadata by file_unique_id. Optionally filter by app_id for isolation."""
    with get_connection() as conn:
        if app_id:
            row = conn.execute(
                "SELECT * FROM files WHERE file_unique_id = ? AND app_id = ? AND is_deleted = 0",
                (file_unique_id, app_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM files WHERE file_unique_id = ? AND is_deleted = 0",
                (file_unique_id,)
            ).fetchone()
        return dict(row) if row else None


def list_files(app_id: str, folder_id: str = "root", limit: int = 50,
               offset: int = 0, search: str | None = None):
    """List files for a specific app in a folder, with optional search."""
    with get_connection() as conn:
        if search:
            row = conn.execute(
                """SELECT COUNT(*) as count FROM files 
                   WHERE app_id = ? AND folder_id = ? AND is_deleted = 0
                   AND file_name LIKE ?""",
                (app_id, folder_id, f"%{search}%")
            ).fetchone()
            total = row["count"] if row else 0

            rows = conn.execute(
                """SELECT * FROM files 
                   WHERE app_id = ? AND folder_id = ? AND is_deleted = 0
                   AND file_name LIKE ?
                   ORDER BY uploaded_at DESC LIMIT ? OFFSET ?""",
                (app_id, folder_id, f"%{search}%", limit, offset)
            ).fetchall()
        else:
            row = conn.execute(
                """SELECT COUNT(*) as count FROM files 
                   WHERE app_id = ? AND folder_id = ? AND is_deleted = 0""",
                (app_id, folder_id)
            ).fetchone()
            total = row["count"] if row else 0

            rows = conn.execute(
                """SELECT * FROM files 
                   WHERE app_id = ? AND folder_id = ? AND is_deleted = 0
                   ORDER BY uploaded_at DESC LIMIT ? OFFSET ?""",
                (app_id, folder_id, limit, offset)
            ).fetchall()

        files = [dict(r) for r in rows]
        return {"files": files, "total": total, "limit": limit, "offset": offset}


def soft_delete_file(file_unique_id: str, app_id: str):
    """Soft delete a file (mark as deleted, don't remove from Telegram)."""
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE files SET is_deleted = 1 WHERE file_unique_id = ? AND app_id = ? AND is_deleted = 0",
            (file_unique_id, app_id)
        )
        return result.rowcount > 0


def rename_file(file_unique_id: str, new_name: str, app_id: str):
    """Rename a file."""
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE files SET file_name = ? WHERE file_unique_id = ? AND app_id = ? AND is_deleted = 0",
            (new_name, file_unique_id, app_id)
        )
        return result.rowcount > 0


def move_file(file_unique_id: str, folder_id: str, app_id: str):
    """Move a file to a different folder."""
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE files SET folder_id = ? WHERE file_unique_id = ? AND app_id = ? AND is_deleted = 0",
            (folder_id, file_unique_id, app_id)
        )
        return result.rowcount > 0


def increment_download_count(file_unique_id: str):
    """Increment download counter for a file."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE files SET download_count = download_count + 1 WHERE file_unique_id = ?",
            (file_unique_id,)
        )


# ── Folder Operations ────────────────────────────────────────────────

def create_folder(name: str, parent_id: str, app_id: str):
    """Create a new folder. Returns the folder_id."""
    # Generate folder_id from parent + name
    safe_name = name.replace(" ", "-").lower()
    if parent_id == "root":
        folder_id = safe_name
    else:
        folder_id = f"{parent_id}/{safe_name}"

    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO folders (folder_id, name, parent_id, app_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (folder_id, name, parent_id, app_id, time.time())
        )
    return folder_id


def list_folders(app_id: str):
    """List all folders for an app with file counts."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT f.folder_id, f.name, f.parent_id, f.created_at,
                      COUNT(fi.id) as file_count
               FROM folders f
               LEFT JOIN files fi ON fi.folder_id = f.folder_id 
                   AND fi.app_id = f.app_id AND fi.is_deleted = 0
               WHERE f.app_id = ?
               GROUP BY f.folder_id
               ORDER BY f.name""",
            (app_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Stats Operations ─────────────────────────────────────────────────

def get_stats(app_id: str | None = None):
    """Get upload statistics. If app_id provided, scoped to that app."""
    with get_connection() as conn:
        if app_id:
            row = conn.execute(
                """SELECT COUNT(*) as total_files, 
                          COALESCE(SUM(file_size), 0) as total_size_bytes,
                          COALESCE(SUM(download_count), 0) as total_downloads
                   FROM files WHERE app_id = ? AND is_deleted = 0""",
                (app_id,)
            ).fetchone()
            app_stats = dict(row) if row else {}

            row2 = conn.execute(
                "SELECT COUNT(*) as folders_count FROM folders WHERE app_id = ?",
                (app_id,)
            ).fetchone()
            app_stats["folders_count"] = row2["folders_count"] if row2 else 0
            app_stats["app_id"] = app_id
            return app_stats
        else:
            row = conn.execute(
                """SELECT COUNT(*) as total_files, 
                          COALESCE(SUM(file_size), 0) as total_size_bytes,
                          COALESCE(SUM(download_count), 0) as total_downloads
                   FROM files WHERE is_deleted = 0"""
            ).fetchone()
            return dict(row) if row else {}
