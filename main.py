"""
Telegram Bridge — HTTP API Server
FastAPI-based HTTP API that accepts file uploads from Cloudflare Workers
and forwards them to Telegram via MTProto.
"""

import asyncio
import logging
import time
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from config import (
    API_SECRET_KEY, ALLOWED_ORIGINS, MAX_FILE_SIZE, 
    BRIDGE_HOST, BRIDGE_PORT, CHANNEL_ID
)
from bridge import bridge
from database import init_db, save_upload, get_upload, get_stats, increment_download_count

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Telegram Bridge API",
    description="MTProto-based file upload bridge to Telegram cloud storage",
    version="1.0.0",
)

# CORS — allow Cloudflare Workers to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth Dependency ───────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Header(...)):
    """Verify the API key from X-API-Key header."""
    if x_api_key != API_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


# ─── Response Models ───────────────────────────────────────────────

class UploadResponse(BaseModel):
    success: bool
    file_id: str
    file_unique_id: str
    file_name: str
    file_size: int
    mime_type: str
    download_url: Optional[str] = None


class ResolveResponse(BaseModel):
    success: bool
    file_id: str
    file_path: str
    file_size: int
    download_url: str


class StatsResponse(BaseModel):
    total_uploads: int
    total_size: int
    total_downloads: int


class HealthResponse(BaseModel):
    status: str
    bot_username: str
    channel_id: int
    uptime_seconds: float


# ─── Startup / Shutdown ────────────────────────────────────────────

START_TIME = 0.0

@app.on_event("startup")
async def startup():
    global START_TIME
    START_TIME = time.time()
    
    # Initialize database
    init_db()
    logger.info("Database initialized.")
    
    # Start MTProto client
    me = await bridge.start()
    logger.info(f"Bridge ready: @{me.username}")


@app.on_event("shutdown")
async def shutdown():
    await bridge.stop()
    logger.info("Bridge shutdown complete.")


# ─── Endpoints ─────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint — no auth required."""
    me = await bridge.client.get_me()
    return HealthResponse(
        status="ok" if bridge.ready else "error",
        bot_username=me.username,
        channel_id=CHANNEL_ID,
        uptime_seconds=time.time() - START_TIME,
    )


@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    app_id: str = Form("prince-snaps"),
    caption: str = Form(""),
    channel_id: Optional[int] = Form(None),
    api_key: str = Depends(verify_api_key),
):
    """
    Upload a file to Telegram via MTProto.
    
    - **file**: The file to upload (max 2 GB)
    - **app_id**: Application identifier for tracking (e.g., "prince-snaps", "bajar-sodai")
    - **caption**: Optional caption for the file
    - **channel_id**: Override default channel ID (optional)
    
    Returns file_id and file_unique_id for storage in your database.
    """
    # Read file data
    file_data = await file.read()
    file_size = len(file_data)
    
    # Validate file size
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {MAX_FILE_SIZE // (1024*1024*1024)} GB"
        )
    
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    
    # Upload to Telegram
    target_channel = channel_id or CHANNEL_ID
    
    result = await bridge.upload_file(
        file_data=file_data,
        file_name=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        channel_id=target_channel,
        app_id=app_id,
    )
    
    # Save to local database
    save_upload(
        file_unique_id=result["file_unique_id"],
        file_id=result["file_id"],
        file_name=result["file_name"],
        file_size=result["file_size"],
        mime_type=result["mime_type"],
        channel_id=target_channel,
        uploaded_by=app_id,
    )
    
    # Generate download URL
    download_url = await bridge.get_download_url(result["file_id"])
    
    return UploadResponse(
        success=True,
        file_id=result["file_id"],
        file_unique_id=result["file_unique_id"],
        file_name=result["file_name"],
        file_size=result["file_size"],
        mime_type=result["mime_type"],
        download_url=download_url,
    )


@app.get("/resolve/{file_unique_id}")
async def resolve_file(
    file_unique_id: str,
    api_key: str = Depends(verify_api_key),
):
    """
    Resolve a file_unique_id to get download URL and metadata.
    Use this to get fresh download URLs for displaying files.
    """
    upload = get_upload(file_unique_id)
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")
    
    download_url = await bridge.get_download_url(upload["file_id"])
    increment_download_count(file_unique_id)
    
    return JSONResponse({
        "success": True,
        "file_id": upload["file_id"],
        "file_unique_id": upload["file_unique_id"],
        "file_name": upload["file_name"],
        "file_size": upload["file_size"],
        "mime_type": upload["mime_type"],
        "download_url": download_url,
        "uploaded_at": upload["uploaded_at"],
        "download_count": upload["download_count"] + 1,
    })


@app.get("/stats", response_model=StatsResponse)
async def stats(api_key: str = Depends(verify_api_key)):
    """Get upload statistics."""
    s = get_stats()
    return StatsResponse(**s)


@app.get("/download/{file_unique_id}")
async def download_file(
    file_unique_id: str,
    api_key: str = Depends(verify_api_key),
):
    """
    Stream a file directly through the bridge.
    This hides the bot token from end users.
    """
    upload = get_upload(file_unique_id)
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_info = await bridge.resolve_file(upload["file_id"])
    
    # Stream the file from Telegram
    async def file_stream():
        async for chunk in bridge.client.download_media(
            file_info["file_path"],
            in_memory=False,
            block=True,
        ):
            yield chunk
    
    increment_download_count(file_unique_id)
    
    return StreamingResponse(
        file_stream(),
        media_type=upload["mime_type"],
        headers={
            "Content-Disposition": f"attachment; filename={upload['file_name']}"
        },
    )


# ─── Run ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=BRIDGE_HOST,
        port=BRIDGE_PORT,
        reload=False,
        workers=1,
        log_level="info",
    )
