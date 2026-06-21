"""
Telegram Bridge — HTTP API Server (v2 — Universal Storage API)
FastAPI-based HTTP API that accepts file uploads from any application
and forwards them to Telegram via MTProto.
"""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Header, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from rate_limit import RateLimitMiddleware
from logging_config import setup_logging

from config import (
    API_SECRET_KEY, ALLOWED_ORIGINS, MAX_FILE_SIZE, UPLOAD_TIMEOUT, CHUNK_SIZE,
    BRIDGE_HOST, BRIDGE_PORT, CHANNEL_ID
)
from bridge import bridge
from database import (
    init_db, save_upload, get_upload, list_files, soft_delete_file,
    rename_file, move_file, create_folder, list_folders, get_stats,
    increment_download_count
)
from api_keys import init_key_table, validate_key
from cache import url_cache
from webhooks import webhook_manager
from quotas import init_quotas_table, check_quota, get_quota, set_quota
from cleanup import cleanup_expired_files

logger = setup_logging("INFO")


# ─── Lifespan (startup / shutdown) ─────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage bridge lifecycle: init DB, start MTProto client, cleanup on exit."""
    init_db()
    init_key_table()
    init_quotas_table()
    logger.info("Database, API key tables, and quotas initialized.")

    me = await bridge.start()
    logger.info(f"Bridge ready: @{me.username}")

    yield

    await bridge.stop()
    url_cache.clear()
    logger.info("Bridge shutdown complete. Cache cleared.")


app = FastAPI(
    title="Telegram Bridge API",
    description="Universal file storage API powered by Telegram cloud storage",
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── Request ID Middleware ──────────────────────────────────────────────

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Attach a unique request ID to every request for tracing."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:12])
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Rate limiting — 60 req/min general, 10 uploads/min (added BEFORE CORS so CORS runs first)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60, upload_rpm=10)

# CORS — allow any origin (public API) — MUST be outermost to handle OPTIONS preflight
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# ─── Auth Dependency ──────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Header(...)):
    """
    Verify the API key from X-API-Key header.
    Supports both the master key (API_SECRET_KEY) and per-app keys.
    """
    # Master key always works
    if x_api_key == API_SECRET_KEY:
        return x_api_key

    # Check per-app keys
    key_info = validate_key(x_api_key)
    if key_info:
        return x_api_key

    raise HTTPException(status_code=401, detail="Invalid API key")


# ─── Response Models ──────────────────────────────────────────────────

class ApiResponse(BaseModel):
    success: bool = True


class UploadResponse(BaseModel):
    success: bool
    file_unique_id: str
    file_name: str
    file_size: int
    mime_type: str
    folder_id: str
    app_id: str
    uploaded_at: float
    download_url: str


class FileInfoResponse(BaseModel):
    success: bool
    file_unique_id: str
    file_name: str
    file_size: int
    mime_type: str
    folder_id: str
    app_id: str
    uploaded_at: float
    download_count: int
    download_url: str


class FileListResponse(BaseModel):
    success: bool
    files: list
    total: int
    limit: int
    offset: int


class FolderInfo(BaseModel):
    folder_id: str
    name: str
    parent_id: str
    file_count: int
    created_at: float


class FolderListResponse(BaseModel):
    success: bool
    folders: list


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: str = "root"


class RenameRequest(BaseModel):
    new_name: str


class MoveRequest(BaseModel):
    folder_id: str


class StatsResponse(BaseModel):
    success: bool
    app_id: Optional[str] = None
    total_files: int = 0
    total_size_bytes: int = 0
    total_downloads: int = 0
    folders_count: int = 0


class HealthResponse(BaseModel):
    status: str
    bot_username: str
    channel_id: int
    uptime_seconds: float


class ErrorResponse(BaseModel):
    success: bool = False
    error: dict


# ─── Health Check (public) ────────────────────────────────────────────

START_TIME = time.time()


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check — no auth required. Verifies Telegram connectivity."""
    try:
        me = await bridge.client.get_me()
        return HealthResponse(
            status="ok" if bridge.ready else "error",
            bot_username=me.username,
            channel_id=CHANNEL_ID,
            uptime_seconds=time.time() - START_TIME,
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="error",
            bot_username="unknown",
            channel_id=CHANNEL_ID,
            uptime_seconds=time.time() - START_TIME,
        )


# ─── Upload ───────────────────────────────────────────────────────────

@app.post("/v1/upload", response_model=UploadResponse, tags=["Files"])
async def upload_file(
    file: UploadFile = File(...),
    app_id: str = Form(...),
    folder_id: str = Form("root"),
    description: str = Form(""),
    channel_id: Optional[int] = Form(None),
    skip_dedup: bool = Form(False),
    api_key: str = Depends(verify_api_key),
):
    """
    Upload a file to Telegram via MTProto.

    - **file**: The file to upload (max 2 GB)
    - **app_id**: Application identifier for data isolation (e.g., "prince-snaps", "drive")
    - **folder_id**: Folder path for organization (default: "root")
    - **description**: Optional description
    - **channel_id**: Override target channel (optional, advanced use)
    - **skip_dedup**: If false, returns existing file if hash matches (default: false)
    """
    file_data = await file.read()
    file_size = len(file_data)

    max_mb = MAX_FILE_SIZE // (1024 * 1024)
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file_size // (1024*1024)} MB). Max size: {max_mb} MB. "
                   f"At current upload speed, {max_mb} MB takes ~{max_mb // 15} minutes."
        )

    if file_size == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Check storage quota
    allowed, quota_info = check_quota(app_id, file_size)
    if not allowed:
        raise HTTPException(
            status_code=413,
            detail=f"Storage quota exceeded for app '{app_id}'. "
                   f"Used: {quota_info['used_bytes']}/{quota_info['max_bytes']} bytes. "
                   f"({quota_info['percent_used']}%)"
        )

    target_channel = channel_id or CHANNEL_ID

    try:
        result = await bridge.upload_file(
            file_data=file_data,
            file_name=file.filename or "unnamed",
            mime_type=file.content_type or "application/octet-stream",
            channel_id=target_channel,
            app_id=app_id,
            folder_id=folder_id,
            description=description or None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"UPLOAD_FAILED app={app_id} file={file.filename} "
            f"size={file_size} mime={file.content_type} "
            f"error={type(e).__name__} detail={e}"
        )
        raise HTTPException(
            status_code=502,
            detail=f"Telegram upload failed: {type(e).__name__}"
        )

    # Save to database
    save_upload(
        file_unique_id=result["file_unique_id"],
        file_id=result["file_id"],
        file_name=result["file_name"],
        file_size=result["file_size"],
        mime_type=result["mime_type"],
        app_id=app_id,
        folder_id=result["folder_id"],
        description=result["description"],
    )

    # Generate download URL and cache it
    download_url = await bridge.get_download_url(result["file_id"])
    url_cache.set(result["file_unique_id"], download_url)

    # Dispatch webhook event
    await webhook_manager.dispatch(
        event="file.uploaded",
        app_id=app_id,
        payload={
            "file_unique_id": result["file_unique_id"],
            "file_name": result["file_name"],
            "file_size": result["file_size"],
            "mime_type": result["mime_type"],
            "folder_id": result["folder_id"],
        }
    )

    return UploadResponse(
        success=True,
        file_unique_id=result["file_unique_id"],
        file_name=result["file_name"],
        file_size=result["file_size"],
        mime_type=result["mime_type"],
        folder_id=result["folder_id"],
        app_id=app_id,
        uploaded_at=time.time(),
        download_url=download_url,
    )


# ─── File Info ────────────────────────────────────────────────────────

@app.get("/v1/files/{file_unique_id}", response_model=FileInfoResponse, tags=["Files"])
async def get_file_info(
    file_unique_id: str,
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """Get metadata for a specific file."""
    upload = get_upload(file_unique_id, app_id=app_id)
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")

    # Try cache first, then fall back to Bot API
    download_url = url_cache.get(file_unique_id)
    if not download_url:
        download_url = await bridge.get_download_url(upload["file_id"])
        url_cache.set(file_unique_id, download_url)

    return FileInfoResponse(
        success=True,
        file_unique_id=upload["file_unique_id"],
        file_name=upload["file_name"],
        file_size=upload["file_size"],
        mime_type=upload["mime_type"],
        folder_id=upload["folder_id"],
        app_id=upload["app_id"],
        uploaded_at=upload["uploaded_at"],
        download_count=upload["download_count"],
        download_url=download_url,
    )


# ─── List Files ───────────────────────────────────────────────────────

@app.get("/v1/files", response_model=FileListResponse, tags=["Files"])
async def list_files_endpoint(
    app_id: str = Query(...),
    folder_id: str = Query("root"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    api_key: str = Depends(verify_api_key),
):
    """
    List files for a specific app in a folder.
    Supports pagination and search by filename.
    """
    result = list_files(
        app_id=app_id,
        folder_id=folder_id,
        limit=limit,
        offset=offset,
        search=search,
    )

    # Add download URLs (from cache when available)
    for f in result["files"]:
        cached_url = url_cache.get(f["file_unique_id"])
        f["download_url"] = cached_url or ""

    return FileListResponse(
        success=True,
        files=result["files"],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
    )


# ─── Delete File ──────────────────────────────────────────────────────

@app.delete("/v1/files/{file_unique_id}", response_model=ApiResponse, tags=["Files"])
async def delete_file(
    file_unique_id: str,
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """Soft delete a file (removes from listing, keeps in Telegram)."""
    deleted = soft_delete_file(file_unique_id, app_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    return ApiResponse(success=True)


# ─── Rename File ──────────────────────────────────────────────────────

@app.patch("/v1/files/{file_unique_id}/rename", response_model=ApiResponse, tags=["Files"])
async def rename_file_endpoint(
    file_unique_id: str,
    body: RenameRequest,
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """Rename a file."""
    renamed = rename_file(file_unique_id, body.new_name, app_id)
    if not renamed:
        raise HTTPException(status_code=404, detail="File not found")
    return ApiResponse(success=True)


# ─── Move File ────────────────────────────────────────────────────────

@app.patch("/v1/files/{file_unique_id}/move", response_model=ApiResponse, tags=["Files"])
async def move_file_endpoint(
    file_unique_id: str,
    body: MoveRequest,
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """Move a file to a different folder."""
    moved = move_file(file_unique_id, body.folder_id, app_id)
    if not moved:
        raise HTTPException(status_code=404, detail="File not found")
    return ApiResponse(success=True)


# ─── Folders ──────────────────────────────────────────────────────────

@app.post("/v1/folders", response_model=ApiResponse, tags=["Folders"])
async def create_folder_endpoint(
    body: CreateFolderRequest,
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """Create a new folder."""
    folder_id = create_folder(body.name, body.parent_id, app_id)
    return ApiResponse(success=True)


@app.get("/v1/folders", response_model=FolderListResponse, tags=["Folders"])
async def list_folders_endpoint(
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """List all folders for an app."""
    folders = list_folders(app_id)
    return FolderListResponse(success=True, folders=folders)


# ─── Resolve URL ──────────────────────────────────────────────────────

@app.get("/v1/resolve/{file_unique_id}", response_model=ApiResponse, tags=["Files"])
async def resolve_file(
    file_unique_id: str,
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """Get a fresh download URL for a file."""
    upload = get_upload(file_unique_id, app_id=app_id)
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")

    # Try cache first, then fall back to Bot API
    download_url = url_cache.get(file_unique_id)
    if not download_url:
        download_url = await bridge.get_download_url(upload["file_id"])
        url_cache.set(file_unique_id, download_url)

    increment_download_count(file_unique_id)

    return JSONResponse({
        "success": True,
        "download_url": download_url,
        "expires_in": 3600,
    })


# ─── Download Redirect ────────────────────────────────────────────────

@app.get("/v1/download/{file_unique_id}", tags=["Files"])
async def download_file(
    file_unique_id: str,
    app_id: str = Query(...),
    api_key: str = Depends(verify_api_key),
):
    """Redirect to the direct Telegram download URL."""
    upload = get_upload(file_unique_id, app_id=app_id)
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")

    increment_download_count(file_unique_id)

    # Try cache first
    download_url = url_cache.get(file_unique_id)
    if not download_url:
        download_url = await bridge.get_download_url(upload["file_id"])
        url_cache.set(file_unique_id, download_url)

    return RedirectResponse(url=download_url)


# ─── Webhook Management ────────────────────────────────────────────────

@app.post("/v1/webhooks", tags=["Webhooks"])
async def register_webhook(
    request: dict,
    api_key: str = Depends(verify_api_key),
):
    """Register a webhook for an app to receive file upload events."""
    app_id = request.get("app_id", "")
    url = request.get("url", "")
    events = request.get("events", ["file.uploaded"])
    secret = request.get("secret", "")

    if not app_id or not url:
        raise HTTPException(status_code=400, detail="app_id and url are required")

    result = webhook_manager.register(app_id, url, events, secret)
    return {"success": True, **result}


@app.delete("/v1/webhooks/{app_id}", tags=["Webhooks"])
async def unregister_webhook(
    app_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Unregister a webhook for an app."""
    removed = webhook_manager.unregister(app_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No webhook found for this app_id")
    return {"success": True, "message": f"Webhook unregistered for {app_id}"}


@app.get("/v1/webhooks", tags=["Webhooks"])
async def list_webhooks(
    api_key: str = Depends(verify_api_key),
):
    """List all registered webhooks (secrets are masked)."""
    return {"success": True, "webhooks": webhook_manager.list_all()}


# ─── Storage Quotas ────────────────────────────────────────────────────

@app.get("/v1/quota/{app_id}", tags=["System"])
async def get_app_quota(
    app_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Get storage quota and usage for an app."""
    quota = get_quota(app_id)
    return {"success": True, **quota}


@app.put("/v1/quota/{app_id}", tags=["System"])
async def update_app_quota(
    app_id: str,
    request: dict,
    api_key: str = Depends(verify_api_key),
):
    """Set storage quota for an app (max_bytes, warn_at_percent)."""
    max_bytes = request.get("max_bytes", 1073741824)
    warn_at_percent = request.get("warn_at_percent", 80)
    quota = set_quota(app_id, max_bytes, warn_at_percent)
    return {"success": True, **quota}


# ─── Cache Management ──────────────────────────────────────────────────

@app.get("/v1/cache/stats", tags=["System"])
async def cache_stats(
    api_key: str = Depends(verify_api_key),
):
    """Get download URL cache statistics."""
    return {"success": True, **url_cache.stats}


@app.post("/v1/cache/clear", tags=["System"])
async def clear_cache(
    api_key: str = Depends(verify_api_key),
):
    """Clear the download URL cache."""
    count = url_cache.clear()
    return {"success": True, "cleared": count}


# ─── Cleanup ───────────────────────────────────────────────────────────

@app.post("/v1/cleanup", tags=["System"])
async def run_cleanup(
    ttl_days: int = Query(30, ge=1, le=365),
    api_key: str = Depends(verify_api_key),
):
    """Manually trigger cleanup of expired soft-deleted files."""
    result = cleanup_expired_files(ttl_seconds=ttl_days * 86400)
    return {"success": True, **result}


# ─── Stats ────────────────────────────────────────────────────────────

@app.get("/v1/stats", response_model=StatsResponse, tags=["System"])
async def stats(
    app_id: Optional[str] = Query(None),
    api_key: str = Depends(verify_api_key),
):
    """Get upload statistics. Optionally filter by app_id."""
    s = get_stats(app_id)
    return StatsResponse(success=True, **s)


# ─── Run ──────────────────────────────────────────────────────────────

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


# ─── Upload Config (public) ───────────────────────────────────────────

@app.get("/v1/config", tags=["System"])
async def get_upload_config():
    """
    Get upload configuration — no auth needed.
    Frontend uses this to show limits, estimated times, chunk size, etc.
    """
    max_mb = MAX_FILE_SIZE // (1024 * 1024)
    return {
        "success": True,
        "max_file_size_bytes": MAX_FILE_SIZE,
        "max_file_size_mb": max_mb,
        "upload_timeout_seconds": UPLOAD_TIMEOUT,
        "upload_timeout_minutes": UPLOAD_TIMEOUT // 60,
        "estimated_upload_speed_mbps": 0.15,
        "estimated_time_per_mb_seconds": 6.7,
        "estimated_time_for_max_mb_minutes": int(max_mb * 6.7 / 60),
        "chunk_size_bytes": CHUNK_SIZE,
        "supported_methods": ["single", "chunked"],
        "note": "Upload speed depends on Telegram MTProto connection. "
                "Estimated 0.15 MB/s. A 500MB file takes ~55 minutes."
    }
