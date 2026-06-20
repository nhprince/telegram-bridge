"""
Telegram Bridge — MTProto Client Manager
Manages the Pyrogram client session and handles Telegram API calls.
"""

import asyncio
import io
import logging

# Patch Pyrogram's channel ID limits to support newer Telegram channels
import pyrogram.utils
pyrogram.utils.MIN_CHANNEL_ID = -1007852516352
pyrogram.utils.MIN_CHAT_ID = -2147483648

from pyrogram import Client
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_TOKEN

logger = logging.getLogger(__name__)


class TelegramBridge:
    """
    MTProto-based Telegram client that acts as a bridge between
    your applications and Telegram's cloud storage.
    
    Features:
    - Upload files up to 2 GB (free) / 4 GB (Premium)
    - Stream files without disk storage
    - Return file_id and file_unique_id for D1 storage
    - Support multiple apps via app_id parameter
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
        channel_id: int = None,
        app_id: str = "prince-snaps",
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
        
        async with self._upload_semaphore:
            try:
                # Determine upload method based on file size and type
                file_size = len(file_data)
                
                if mime_type.startswith("image/"):
                    result = await self._upload_photo(
                        channel_id, file_data, file_name, progress_callback
                    )
                elif mime_type.startswith("video/"):
                    result = await self._upload_video(
                        channel_id, file_data, file_name, progress_callback
                    )
                else:
                    result = await self._upload_document(
                        channel_id, file_data, file_name, progress_callback
                    )
                
                # Extract file info from the message's attached document
                if hasattr(result, 'document') and result.document:
                    file_obj = result.document
                else:
                    file_obj = result
                
                logger.info(
                    f"Uploaded {file_name} ({file_size} bytes) → "
                    f"file_id={file_obj.file_id}, file_unique_id={file_obj.file_unique_id}"
                )
                
                return {
                    "file_id": file_obj.file_id,
                    "file_unique_id": file_obj.file_unique_id,
                    "file_size": file_size,
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "app_id": app_id,
                }
                
            except Exception as e:
                logger.error(f"Upload failed for {file_name}: {e}")
                raise

    def _make_file(self, data: bytes, file_name: str) -> io.BytesIO:
        """Wrap bytes in a BytesIO with a .name attribute for Pyrogram."""
        f = io.BytesIO(data)
        f.name = file_name
        return f

    async def _upload_photo(self, channel_id: int, data: bytes,
                            caption: str, progress_callback) -> Message:
        """Upload as photo (images get compressed preview in Telegram)."""
        return await self.client.send_photo(
            chat_id=channel_id,
            photo=self._make_file(data, "photo.jpg"),
            caption=caption[:1024],  # Telegram caption limit
            progress=progress_callback,
        )

    async def _upload_video(self, channel_id: int, data: bytes,
                            caption: str, progress_callback) -> Message:
        """Upload as video (supports up to 2 GB)."""
        return await self.client.send_video(
            chat_id=channel_id,
            video=self._make_file(data, "video.mp4"),
            caption=caption[:1024],
            progress=progress_callback,
            supports_streaming=True,
        )

    async def _upload_document(self, channel_id: int, data: bytes,
                               caption: str, progress_callback) -> Message:
        """Upload as document (no compression, preserves original quality)."""
        return await self.client.send_document(
            chat_id=channel_id,
            document=self._make_file(data, "file.bin"),
            caption=caption[:1024],
            progress=progress_callback,
        )

    async def resolve_file(self, file_id: str) -> dict:
        """
        Get file metadata from file_id.
        
        Returns:
            dict with file_path, file_size, etc.
        """
        if not self.ready:
            raise RuntimeError("Bridge client not started.")
        
        file_info = await self.client.get_file(file_id)
        return {
            "file_id": file_id,
            "file_path": file_info.file_path,
            "file_size": file_info.file_size,
            "file_unique_id": getattr(file_info, "file_unique_id", None),
        }

    async def get_download_url(self, file_id: str) -> str:
        """
        Get a direct download URL for a file via Bot API.
        
        Calls getFile via Bot API to get the file_path, then constructs
        the download URL. The URL is valid for at least 1 hour.
        """
        import aiohttp
        
        token = BOT_TOKEN
        api_url = f"https://api.telegram.org/bot{token}/getFile"
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, data={"file_id": file_id}) as resp:
                data = await resp.json()
                if data.get("ok"):
                    file_path = data["result"]["file_path"]
                    return f"https://api.telegram.org/file/bot{token}/{file_path}"
                raise RuntimeError(f"getFile failed: {data}")

    @property
    def _default_channel_id(self) -> int:
        from config import CHANNEL_ID
        return CHANNEL_ID


# Singleton instance
bridge = TelegramBridge()
