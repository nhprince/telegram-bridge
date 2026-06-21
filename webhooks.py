"""
Telegram Bridge — Webhook Event System
Allows apps to register webhooks and receive events on file uploads.
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


class WebhookManager:
    """
    Manages webhook registrations and dispatches events to registered URLs.
    
    Events:
    - file.uploaded: When a file upload completes successfully
    - file.deleted: When a file is soft-deleted
    """

    def __init__(self):
        self._webhooks: dict[str, dict] = {}  # app_id -> {url, secret, events, created_at}

    def register(self, app_id: str, url: str, events: list[str] = None, secret: str = "") -> dict:
        """
        Register a webhook for an app.
        
        Args:
            app_id: Application identifier
            url: HTTPS URL to receive webhook events
            events: List of events to subscribe to (default: ["file.uploaded"])
            secret: Secret for HMAC signature verification
        """
        if not url.startswith("https://"):
            raise ValueError("Webhook URL must use HTTPS")

        webhook = {
            "app_id": app_id,
            "url": url,
            "secret": secret or hashlib.sha256(f"{app_id}{time.time()}".encode()).hexdigest()[:32],
            "events": events or ["file.uploaded"],
            "created_at": time.time(),
            "last_triggered": None,
            "trigger_count": 0,
        }
        self._webhooks[app_id] = webhook
        logger.info(f"Webhook registered for {app_id} -> {url}")
        return {"app_id": app_id, "secret": webhook["secret"], "events": webhook["events"]}

    def unregister(self, app_id: str) -> bool:
        """Remove a webhook registration."""
        if app_id in self._webhooks:
            del self._webhooks[app_id]
            logger.info(f"Webhook unregistered for {app_id}")
            return True
        return False

    def get(self, app_id: str) -> Optional[dict]:
        """Get webhook info for an app."""
        return self._webhooks.get(app_id)

    def list_all(self) -> list[dict]:
        """List all registered webhooks."""
        return [
            {**w, "secret": "***"}  # Don't expose secret in listings
            for w in self._webhooks.values()
        ]

    async def dispatch(self, event: str, app_id: str, payload: dict) -> bool:
        """
        Dispatch an event to the registered webhook.
        
        Args:
            event: Event name (e.g., "file.uploaded")
            app_id: Application identifier
            payload: Event data to send
        """
        webhook = self._webhooks.get(app_id)
        if not webhook:
            return False

        if event not in webhook["events"]:
            return False

        # Build event body
        body = json.dumps({
            "event": event,
            "app_id": app_id,
            "timestamp": time.time(),
            "data": payload,
        }).encode()

        # Generate HMAC signature
        signature = hmac.new(
            webhook["secret"].encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        try:
            req = Request(
                webhook["url"],
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Bridge-Event": event,
                    "X-Bridge-Signature": signature,
                    "User-Agent": "TelegramBridge/2.1",
                },
                method="POST",
            )
            # Use non-blocking approach for async context
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: urlopen(req, timeout=10),
            )
            status = response.getcode()

            webhook["last_triggered"] = time.time()
            webhook["trigger_count"] += 1

            if 200 <= status < 300:
                logger.info(f"Webhook delivered: {event} -> {app_id} ({status})")
                return True
            else:
                logger.warning(f"Webhook failed: {event} -> {app_id} (HTTP {status})")
                return False

        except (URLError, OSError) as e:
            logger.warning(f"Webhook delivery error: {event} -> {app_id}: {e}")
            return False


# Singleton instance
webhook_manager = WebhookManager()
