"""
Telegram Bridge — Test Suite v2.1
Tests for new features: caching, webhooks, quotas, cleanup, request IDs, lifespan.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["API_SECRET_KEY"] = "test-key-123"
os.environ["ALLOWED_ORIGINS"] = "*"
os.environ["DATABASE_PATH"] = ":memory:"

from cache import DownloadURLCache
from webhooks import WebhookManager
from quotas import init_quotas_table, check_quota, get_quota, set_quota
from cleanup import cleanup_expired_files, get_expired_file_count
from database import init_db, save_upload, get_connection


class TestDownloadURLCache(unittest.TestCase):
    """Test the download URL cache."""

    def setUp(self):
        self.cache = DownloadURLCache(ttl_seconds=5, max_size=10)

    def test_set_and_get(self):
        self.cache.set("file1", "https://example.com/file1")
        self.assertEqual(self.cache.get("file1"), "https://example.com/file1")

    def test_get_missing(self):
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_expiry(self):
        cache = DownloadURLCache(ttl_seconds=1, max_size=10)
        cache.set("file1", "https://example.com/file1")
        time.sleep(1.1)
        self.assertIsNone(cache.get("file1"))

    def test_invalidate(self):
        self.cache.set("file1", "https://example.com/file1")
        self.assertTrue(self.cache.invalidate("file1"))
        self.assertIsNone(self.cache.get("file1"))

    def test_invalidate_missing(self):
        self.assertFalse(self.cache.invalidate("nonexistent"))

    def test_clear(self):
        for i in range(5):
            self.cache.set(f"file{i}", f"https://example.com/file{i}")
        cleared = self.cache.clear()
        self.assertEqual(cleared, 5)

    def test_max_size_eviction(self):
        cache = DownloadURLCache(ttl_seconds=60, max_size=3)
        cache.set("file1", "https://example.com/1")
        cache.set("file2", "https://example.com/2")
        cache.set("file3", "https://example.com/3")
        cache.set("file4", "https://example.com/4")  # Should evict file1
        self.assertIsNone(cache.get("file1"))
        self.assertIsNotNone(cache.get("file4"))

    def test_stats(self):
        self.cache.set("file1", "https://example.com/1")
        self.cache.get("file1")  # hit
        self.cache.get("missing")  # miss
        stats = self.cache.stats
        self.assertEqual(stats["size"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertAlmostEqual(stats["hit_rate"], 0.5, places=1)

    def test_cleanup_expired(self):
        cache = DownloadURLCache(ttl_seconds=1, max_size=10)
        cache.set("file1", "https://example.com/1")
        cache.set("file2", "https://example.com/2")
        time.sleep(1.1)
        removed = cache.cleanup()
        self.assertEqual(removed, 2)


class TestWebhookManager(unittest.TestCase):
    """Test the webhook event system."""

    def setUp(self):
        self.webhooks = WebhookManager()

    def test_register_webhook(self):
        result = self.webhooks.register("test-app", "https://example.com/webhook")
        self.assertEqual(result["app_id"], "test-app")
        self.assertIn("secret", result)
        self.assertEqual(result["events"], ["file.uploaded"])

    def test_register_with_custom_events(self):
        result = self.webhooks.register(
            "test-app", "https://example.com/hook",
            events=["file.uploaded", "file.deleted"]
        )
        self.assertEqual(result["events"], ["file.uploaded", "file.deleted"])

    def test_register_invalid_url(self):
        with self.assertRaises(ValueError):
            self.webhooks.register("test-app", "http://example.com/webhook")

    def test_unregister(self):
        self.webhooks.register("test-app", "https://example.com/webhook")
        self.assertTrue(self.webhooks.unregister("test-app"))
        self.assertFalse(self.webhooks.unregister("test-app"))

    def test_get(self):
        self.webhooks.register("test-app", "https://example.com/webhook")
        info = self.webhooks.get("test-app")
        self.assertIsNotNone(info)
        self.assertEqual(info["url"], "https://example.com/webhook")

    def test_list_all(self):
        self.webhooks.register("app1", "https://example.com/1")
        self.webhooks.register("app2", "https://example.com/2")
        listing = self.webhooks.list_all()
        self.assertEqual(len(listing), 2)
        # Secrets should be masked
        for w in listing:
            self.assertEqual(w["secret"], "***")

    def test_dispatch_no_webhook(self):
        """Dispatch returns False when no webhook registered."""
        result = asyncio.get_event_loop().run_until_complete(
            self.webhooks.dispatch("file.uploaded", "unknown", {})
        )
        self.assertFalse(result)

    def test_dispatch_wrong_event(self):
        """Dispatch returns False when event not subscribed."""
        self.webhooks.register("test-app", "https://example.com/hook", events=["file.deleted"])
        result = asyncio.get_event_loop().run_until_complete(
            self.webhooks.dispatch("file.uploaded", "test-app", {})
        )
        self.assertFalse(result)

    @patch("webhooks.urlopen")
    def test_dispatch_success(self, mock_urlopen):
        """Dispatch returns True on successful delivery."""
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_urlopen.return_value = mock_response

        self.webhooks.register("test-app", "https://example.com/hook")
        result = asyncio.get_event_loop().run_until_complete(
            self.webhooks.dispatch("file.uploaded", "test-app", {"file_unique_id": "abc"})
        )
        self.assertTrue(result)

    @patch("webhooks.urlopen")
    def test_dispatch_failure(self, mock_urlopen):
        """Dispatch returns False on delivery failure."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        self.webhooks.register("test-app", "https://example.com/hook")
        result = asyncio.get_event_loop().run_until_complete(
            self.webhooks.dispatch("file.uploaded", "test-app", {})
        )
        self.assertFalse(result)


class TestStorageQuotas(unittest.TestCase):
    """Test per-app storage quotas."""

    @classmethod
    def setUpClass(cls):
        init_db()
        init_quotas_table()

    def setUp(self):
        with get_connection() as conn:
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM storage_quotas")

    def test_default_quota(self):
        quota = get_quota("test-app")
        self.assertEqual(quota["max_bytes"], 1073741824)  # 1GB default
        self.assertEqual(quota["used_bytes"], 0)
        self.assertTrue(quota["within_limit"])

    def test_set_quota(self):
        quota = set_quota("test-app", 1024 * 1024, 90)  # 1MB
        self.assertEqual(quota["max_bytes"], 1048576)
        self.assertEqual(quota["warn_at_percent"], 90)

    def test_check_quota_allows(self):
        set_quota("test-app", 1024 * 1024)  # 1MB
        allowed, info = check_quota("test-app", 500)
        self.assertTrue(allowed)

    def test_check_quota_rejects(self):
        set_quota("test-app", 100)  # 100 bytes
        # Upload a file first
        save_upload("uid1", "fid1", "test.jpg", 50, "image/jpeg", "test-app")
        allowed, info = check_quota("test-app", 60)  # Would exceed 100 bytes
        self.assertFalse(allowed)

    def test_quota_with_usage(self):
        set_quota("test-app", 1000)
        save_upload("uid1", "fid1", "test.jpg", 500, "image/jpeg", "test-app")
        quota = get_quota("test-app")
        self.assertEqual(quota["used_bytes"], 500)
        self.assertEqual(quota["percent_used"], 50.0)
        self.assertTrue(quota["within_limit"])


class TestCleanup(unittest.TestCase):
    """Test cleanup of expired soft-deleted files."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        with get_connection() as conn:
            conn.execute("DELETE FROM files")

    def test_cleanup_expired_files(self):
        # Insert a soft-deleted file with old timestamp
        old_time = time.time() - (31 * 86400)  # 31 days ago
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO files (file_unique_id, file_id, file_name, file_size, 
                   mime_type, app_id, uploaded_at, is_deleted) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                ("old-uid", "old-fid", "old.jpg", 100, "image/jpeg", "test-app", old_time)
            )
            # Insert a recent soft-deleted file
            conn.execute(
                """INSERT INTO files (file_unique_id, file_id, file_name, file_size, 
                   mime_type, app_id, uploaded_at, is_deleted) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                ("new-uid", "new-fid", "new.jpg", 100, "image/jpeg", "test-app", time.time())
            )

        result = cleanup_expired_files(ttl_seconds=2592000)  # 30 days
        self.assertEqual(result["expired_files_removed"], 1)

        # Verify old file is gone
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM files WHERE file_unique_id = 'old-uid'").fetchone()
            self.assertIsNone(row)

        # Verify new file is still there
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM files WHERE file_unique_id = 'new-uid'").fetchone()
            self.assertIsNotNone(row)

    def test_expired_file_count(self):
        old_time = time.time() - (31 * 86400)
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO files (file_unique_id, file_id, file_name, file_size, 
                   mime_type, app_id, uploaded_at, is_deleted) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                ("expired-uid", "efid", "expired.jpg", 100, "image/jpeg", "test-app", old_time)
            )
        count = get_expired_file_count(ttl_seconds=2592000)
        self.assertEqual(count, 1)


class TestRequestIDMiddleware(unittest.TestCase):
    """Test request ID middleware via the actual FastAPI app."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from main import app
        cls.client = TestClient(app)

    def test_request_id_returned(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("x-request-id", response.headers)

    def test_custom_request_id(self):
        response = self.client.get("/health", headers={"X-Request-ID": "my-custom-id"})
        self.assertEqual(response.headers["x-request-id"], "my-custom-id")


if __name__ == "__main__":
    unittest.main()
