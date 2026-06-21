"""
Telegram Bridge — MTProto Client Manager (v2 — Universal Storage API)
Manages the Pyrogram client session and handles Telegram API calls.
"""

import asyncio
import io
import logging

from fastapi import HTTPException

# Patch Pyrogram's channel ID limits to support newer Telegram channels
import pyrogram.utils
pyrogram.utils.MIN_CHANNEL_ID = -1007852516352
pyrogram.utils.MIN_CHAT_ID = -2147483648

from pyrogram import Client
from pyrogram import raw as praw
from pyrogram.types import Message
from pyrogram.errors.exceptions.bad_request_400 import PhotoSaveFileInvalid
from config import API_ID, API_HASH, BOT_TOKEN

logger = logging.getLogger(__name__)


class TelegramBridge:
    """
    MTProto-based Telegram client that acts as a universal bridge between
    your applications and Telegram's cloud storage.

    Features:
    - Upload files up to 2 GB (free) / 4 GB (Premium)
    - Stream files without disk storage
    - Return file_id and file_unique_id for D1 storage
    - Support multiple apps via app_id parameter
    - Folder organization, rename, soft-delete
    """

    def __init__(self):
        self.client = None
        self.ready = False
        self._upload_semaphore = asyncio.Semaphore(3)  # Max 3 concurrent uploads

    async def start(self):
        """Initialize and start the MTProto client."""
        logger.info("Starting Telegram Bridge client...")

        self.client = Client(
            name="telegram-bridge",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=4,
            max_concurrent_transmissions=3,
        )

        await self.client.start()
        self.ready = True

        # Verify bot info
        me = await self.client.get_me()
        logger.info(f"Bridge bot started: @{me.username} (ID: {me.id})")

        return me

    async def stop(self):
        """Gracefully stop the client."""
        if self.client:
            await self.client.stop()
            self.ready = False
            logger.info("Telegram Bridge client stopped.")

    async def upload_file(
        self,
        file_data: bytes,
        file_name: str,
        mime_type: str = "application/octet-stream",
        channel_id: int | None = None,
        app_id: str = "prince-snaps",
        folder_id: str = "root",
        description: str | None = None,
        progress_callback=None,
    ) -> dict:
        """
        Upload a file to Telegram via MTProto.

        Args:
            file_data: Raw file bytes
            file_name: Original filename
            mime_type: MIME type of the file
            channel_id: Target channel ID (defaults to config)
            app_id: Application identifier for tracking
            folder_id: Folder path for organization (e.g., "photos/2026")
            description: Optional description
            progress_callback: Optional callback(bytes_sent, total_bytes)

        Returns:
            dict with file_id, file_unique_id, file_size, etc.

        Raises:
            ValueError: If file exceeds size limit
            RuntimeError: If upload fails
        """
        if not self.ready:
            raise RuntimeError("Bridge client not started. Call start() first.")

        channel_id = channel_id or self._default_channel_id
        file_size = len(file_data)

        # Log upload start with estimated time
        estimated_seconds = file_size / (0.15 * 1024 * 1024)  # 0.15 MB/s
        logger.info(
            f"UPLOAD_START file={file_name} size={file_size} "
            f"mime={mime_type} est_time={estimated_seconds:.0f}s"
        )

        async with self._upload_semaphore:
            try:
                # Determine upload method based on file size and type
                PHOTO_LIMIT = 5 * 1024 * 1024  # 5 MB

                if mime_type.startswith("image/") and file_size <= PHOTO_LIMIT:
                    try:
                        result = await self._upload_photo(
                            channel_id, file_data, description, progress_callback
                        )
                    except PhotoSaveFileInvalid:
                        logger.warning(
                            f"Photo upload rejected for {file_name} "
                            f"({file_size} bytes), retrying as document"
                        )
                        result = await self._upload_document(
                            channel_id, file_data, description, progress_callback, file_name
                        )
                elif mime_type.startswith("video/"):
                    result = await self._upload_video(
                        channel_id, file_data, description, progress_callback
                    )
                else:
                    result = await self._upload_document(
                        channel_id, file_data, description, progress_callback, file_name
                    )

                file_obj = self._extract_media(result)

                # Extract MTProto download metadata
                # Pyrogram's high-level Document doesn't expose access_hash/dc_id.
                # We use the raw channels.GetMessages API to get the full document metadata.
                access_hash = 0
                file_reference = b""
                dc_id = 0
                media_id = 0

                if file_obj.file_id:
                    try:
                        peer = await self.client.resolve_peer(channel_id)
                        # Retry up to 3 times — message may not be immediately available
                        for attempt in range(3):
                            raw_msgs = await self.client.invoke(
                                praw.functions.channels.GetMessages(
                                    channel=peer,
                                    id=[praw.types.InputMessageID(id=result.id)],
                                )
                            )
                            if raw_msgs and hasattr(raw_msgs, 'messages'):
                                found = False
                                for raw_msg in raw_msgs.messages:
                                    if hasattr(raw_msg, 'media') and raw_msg.media:
                                        media = raw_msg.media
                                        if hasattr(media, 'document') and media.document:
                                            doc = media.document
                                            media_id = doc.id
                                            access_hash = doc.access_hash
                                            file_reference = doc.file_reference
                                            dc_id = doc.dc_id
                                            found = True
                                            break
                                if found:
                                    break
                            if attempt < 2:
                                await asyncio.sleep(1)
                        else:
                            logger.warning(f"channels.GetMessages returned no document after 3 attempts")
                    except Exception as e:
                        logger.warning(f"Could not fetch raw message for access_hash: {type(e).__name__}: {e}")

                logger.info(
                    f"UPLOAD_OK file={file_name} size={file_size} "
                    f"file_id={file_obj.file_id} file_unique_id={file_obj.file_unique_id} "
                    f"media_id={media_id} access_hash={access_hash} dc_id={dc_id}"
                )

                return {
                    "file_id": file_obj.file_id,
                    "file_unique_id": file_obj.file_unique_id,
                    "file_size": file_size,
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "app_id": app_id,
                    "folder_id": folder_id,
                    "description": description,
                    "media_id": media_id,
                    "access_hash": access_hash,
                    "file_reference": file_reference,
                    "dc_id": dc_id,
                }

            except PhotoSaveFileInvalid as e:
                logger.error(
                    f"UPLOAD_FAILED file={file_name} size={file_size} "
                    f"mime={mime_type} error=PhotoSaveFileInvalid detail={e}"
                )
                raise
            except asyncio.TimeoutError:
                logger.error(
                    f"UPLOAD_TIMEOUT file={file_name} size={file_size} "
                    f"est_time={estimated_seconds:.0f}s"
                )
                raise HTTPException(
                    status_code=504,
                    detail=f"Upload timed out. File may be too large for current network speed. "
                           f"Estimated time: {estimated_seconds:.0f}s for {file_size // (1024*1024)} MB."
                )
            except Exception as e:
                logger.error(
                    f"UPLOAD_FAILED file={file_name} size={file_size} "
                    f"mime={mime_type} error={type(e).__name__} detail={e}"
                )
                raise

    def _extract_media(self, result: Message):
        """Extract media object from a Telegram Message (handles all types)."""
        for attr in ('document', 'photo', 'video', 'audio', 'voice', 'video_note', 'sticker', 'animation'):
            media = getattr(result, attr, None)
            if media is None:
                continue
            # Telegram photo is a list of sizes — use the largest (last) one
            if attr == 'photo' and isinstance(media, list):
                media = media[-1]
            if hasattr(media, 'file_id'):
                return media
        return result

    def _make_file(self, data: bytes, file_name: str) -> io.BytesIO:
        """Wrap bytes in a BytesIO with a .name attribute for Pyrogram."""
        f = io.BytesIO(data)
        # Use file_name if provided, otherwise infer from mime
        f.name = file_name if file_name else "upload.bin"
        return f

    async def _upload_photo(self, channel_id: int, data: bytes,
                            caption: str | None, progress_callback) -> Message:
        """Upload as photo (images get compressed preview in Telegram)."""
        return await self.client.send_photo(
            chat_id=channel_id,
            photo=self._make_file(data, "photo.jpg"),
            caption=caption[:1024] if caption else "",
            progress=progress_callback,
        )

    async def _upload_video(self, channel_id: int, data: bytes,
                            caption: str | None, progress_callback) -> Message:
        """Upload as video (supports up to 2 GB)."""
        return await self.client.send_video(
            chat_id=channel_id,
            video=self._make_file(data, "video.mp4"),
            caption=caption[:1024] if caption else "",
            progress=progress_callback,
            supports_streaming=True,
        )

    async def _upload_document(self, channel_id: int, data: bytes,
                               caption: str | None, progress_callback,
                               file_name: str | None = None) -> Message:
        """Upload as document (no compression, preserves original quality)."""
        return await self.client.send_document(
            chat_id=channel_id,
            document=self._make_file(data, file_name or "upload.bin"),
            caption=caption[:1024] if caption else "",
            progress=progress_callback,
        )

    async def resolve_file(self, file_id: str) -> dict:
        """
        Get file metadata from file_id via Bot API.
        Falls back to filename-only if Bot API rejects large files.
        """
        import aiohttp

        if not self.ready:
            raise RuntimeError("Bridge client not started.")

        token = BOT_TOKEN
        api_url = f"https://api.telegram.org/bot{token}/getFile"

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_url, data={"file_id": file_id}) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        result = data["result"]
                        return {
                            "file_id": file_id,
                            "file_path": result["file_path"],
                            "file_size": result.get("file_size", 0),
                            "file_unique_id": result.get("file_unique_id", None),
                        }
                    logger.warning(f"Bot API getFile rejected: {data.get('description', 'unknown')}")
                    return {"file_id": file_id, "file_path": "", "file_size": 0, "file_unique_id": None}
        except Exception as e:
            logger.warning(f"Bot API getFile failed: {e}")
            return {"file_id": file_id, "file_path": "", "file_size": 0, "file_unique_id": None}

    async def get_download_url(self, file_id: str) -> str:
        """
        Get a direct download URL for a file via Bot API.

        Calls getFile via Bot API (HTTP) to get the file_path, then constructs
        the download URL. Falls back to file_id-only if Bot API rejects large files.

        Note: Bot API getFile has a ~20MB limit. For larger files, the download_url
        will be empty and the client should use the file_id with MTProxy or stream
        through the bridge.
        """
        import aiohttp

        token = BOT_TOKEN
        api_url = f"https://api.telegram.org/bot{token}/getFile"

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_url, data={"file_id": file_id}) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        file_path = data["result"]["file_path"]
                        return f"https://api.telegram.org/file/bot{token}/{file_path}"
                    # Bot API refused (e.g. "file is too big") — return empty string
                    # Client should use file_id for MTProto download instead
                    error_desc = data.get("description", "unknown")
                    logger.warning(
                        f"Bot API getFile rejected file_id={file_id}: {error_desc}. "
                        f"Download URL will be empty — client must use file_id."
                    )
                    return ""
        except asyncio.TimeoutError:
            logger.warning(f"Bot API getFile timed out for file_id={file_id}")
            return ""
        except Exception as e:
            logger.warning(f"Bot API getFile failed for file_id={file_id}: {e}")
            return ""

    @property
    def _default_channel_id(self) -> int:
        from config import CHANNEL_ID
        return CHANNEL_ID


# Singleton instance
bridge = TelegramBridge()
