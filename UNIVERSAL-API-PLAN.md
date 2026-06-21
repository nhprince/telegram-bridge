# Universal File Storage API — Detailed Build Plan

> **Goal**: Transform the Telegram Bridge from a single-purpose uploader into a **universal, self-serve file storage API** that any application can use — seamlessly, securely, with data isolation.

---

## Current State (What Exists)

| Component | Status |
|---|---|
| MTProto upload (photo/video/document) | ✅ Working |
| BytesIO name fix | ✅ Fixed |
| Multi-type media detection (photo/video/audio/voice/etc.) | ✅ Fixed |
| SQLite tracking with app_id | ✅ Working |
| Resolve file by file_unique_id | ✅ Working |
| Download redirect (307) | ✅ Working |
| Health check + stats | ✅ Working |
| API key auth | ✅ Working |
| CORS for Cloudflare Workers | ✅ Working |

## What's Missing (The Gap)

| Feature | Why Needed |
|---|---|
| **No file listing by app_id** | Apps can't see their own files |
| **No delete** | Files accumulate forever |
| **No folder organization** | Can't group files |
| **No rename** | Can't organize after upload |
| **No search** | Can't find files |
| **No public domain** | Only accessible via IP:port |
| **No rate limiting** | Anyone with key can flood |
| **No per-app stats** | Can't track usage per app |
| **No API key management** | One key for all apps |
| **No documentation** | No one else can adopt it |

---

## Architecture — The Universal Storage API

```
                    ┌─────────────────────────────────────┐
                    │   bridge-api.nhprince.dpdns.org      │
                    │   (Cloudflare Tunnel → VPS:9000)    │
                    └──────────┬──────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
   ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────┐
   │ Prince Snaps│     │ InstaPrince │     │  Drive App  │
   │ app_id=     │     │ app_id=     │     │ app_id=     │
   │ prince-snaps│     │ instaprince │     │ drive       │
   └─────────────┘     └─────────────┘     └─────────────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   SQLite Database   │
                    │   (isolated by      │
                    │    app_id +         │
                    │    file_unique_id)  │
                    └─────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Telegram Channel   │
                    │  (actual file       │
                    │   storage — 2GB     │
                    │   per file, free)   │
                    └─────────────────────┘
```

### Data Isolation Model

Every file is tagged with `app_id`. Queries always filter by `app_id`. **An app can NEVER see another app's files.**

```sql
-- Prince Snaps queries: WHERE uploaded_by = 'prince-snaps'
-- InstaPrince queries: WHERE uploaded_by = 'instaprince'
-- Drive queries: WHERE uploaded_by = 'drive'
```

---

## API Design — RESTful + Predictable

### Authentication

```
Authorization: Bearer <API_KEY>
```

Per-app API keys (future: generate per-app keys via admin endpoint).

### Endpoints

#### 1. Upload File
```
POST /v1/upload
Content-Type: multipart/form-data

Fields:
  file: binary (required, max 2GB)
  app_id: string (required — "prince-snaps", "instaprince", "drive")
  folder_id: string (optional — default "root")
  description: string (optional)

Response:
{
  "success": true,
  "data": {
    "file_unique_id": "AgADBQADr6cxG...",
    "file_name": "photo.jpg",
    "file_size": 245678,
    "mime_type": "image/jpeg",
    "folder_id": "root",
    "app_id": "prince-snaps",
    "uploaded_at": 1718800000,
    "download_url": "https://bridge-api.nhprince.dpdns.org/v1/download/AgADBQADr6cxG..."
  }
}
```

#### 2. List Files (by App)
```
GET /v1/files?app_id=prince-snaps&folder_id=root&limit=50&offset=0

Response:
{
  "success": true,
  "data": {
    "files": [
      {
        "file_unique_id": "AgADBQADr6cxG...",
        "file_name": "photo.jpg",
        "file_size": 245678,
        "mime_type": "image/jpeg",
        "folder_id": "root",
        "uploaded_at": 1718800000,
        "download_url": "https://bridge-api.nhprince.dpdns.org/v1/download/AgADBQADr6cxG..."
      }
    ],
    "total": 42,
    "limit": 50,
    "offset": 0
  }
}
```

#### 3. Get File Info
```
GET /v1/files/{file_unique_id}?app_id=prince-snaps

Response:
{
  "success": true,
  "data": {
    "file_unique_id": "AgADBQADr6cxG...",
    "file_name": "photo.jpg",
    "file_size": 245678,
    "mime_type": "image/jpeg",
    "folder_id": "root",
    "app_id": "prince-snaps",
    "uploaded_at": 1718800000,
    "download_count": 5,
    "download_url": "https://bridge-api.nhprince.dpdns.org/v1/download/AgADBQADr6cxG..."
  }
}
```

#### 4. Delete File
```
DELETE /v1/files/{file_unique_id}?app_id=prince-snaps

Response:
{
  "success": true,
  "message": "File deleted"
}
```

#### 5. Rename File
```
PATCH /v1/files/{file_unique_id}/rename?app_id=prince-snaps
Content-Type: application/json

{
  "new_name": "vacation-photo.jpg"
}

Response:
{
  "success": true,
  "data": {
    "file_unique_id": "AgADBQADr6cxG...",
    "file_name": "vacation-photo.jpg"
  }
}
```

#### 6. Move to Folder
```
PATCH /v1/files/{file_unique_id}/move?app_id=prince-snaps
Content-Type: application/json

{
  "folder_id": "photos/2026"
}

Response:
{
  "success": true
}
```

#### 7. Create Folder
```
POST /v1/folders?app_id=prince-snaps
Content-Type: application/json

{
  "name": "vacation-2026",
  "parent_id": "root"
}

Response:
{
  "success": true,
  "data": {
    "folder_id": "photos/vacation-2026",
    "name": "vacation-2026",
    "parent_id": "photos",
    "created_at": 1718800000
  }
}
```

#### 8. List Folders
```
GET /v1/folders?app_id=prince-snaps

Response:
{
  "success": true,
  "data": {
    "folders": [
      {
        "folder_id": "root",
        "name": "Root",
        "file_count": 42
      },
      {
        "folder_id": "photos",
        "name": "Photos",
        "file_count": 15
      }
    ]
  }
}
```

#### 9. Download File
```
GET /v1/download/{file_unique_id}?app_id=prince-snaps

Response: 307 Redirect → Telegram CDN URL
```

#### 10. Resolve URL (fresh, doesn't count as download)
```
GET /v1/resolve/{file_unique_id}?app_id=prince-snaps

Response:
{
  "success": true,
  "data": {
    "download_url": "https://api.telegram.org/file/bot.../...",
    "expires_in": 3600
  }
}
```

#### 11. App Stats
```
GET /v1/stats?app_id=prince-snaps

Response:
{
  "success": true,
  "data": {
    "app_id": "prince-snaps",
    "total_files": 42,
    "total_size_bytes": 156789000,
    "total_downloads": 128,
    "folders_count": 5
  }
}
```

#### 12. Health Check (public)
```
GET /health

Response:
{
  "status": "ok",
  "bot_username": "@NhStorageapiprincebot",
  "uptime_seconds": 86400
}
```

---

## Database Schema (Updated)

```sql
-- Files table (extended)
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
    uploaded_by TEXT,           -- future: user_id for multi-user
    uploaded_at REAL NOT NULL,
    download_count INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0  -- soft delete
);

-- Folders table
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    parent_id TEXT DEFAULT 'root',
    app_id TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- Apps table (for tracking registered apps)
CREATE TABLE IF NOT EXISTS apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT UNIQUE NOT NULL,
    name TEXT,
    description TEXT,
    created_at REAL NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_files_app_id ON files(app_id);
CREATE INDEX IF NOT EXISTS idx_files_folder ON files(app_id, folder_id);
CREATE INDEX IF NOT EXISTS idx_files_uploaded_at ON files(app_id, uploaded_at);
CREATE INDEX IF NOT EXISTS idx_folders_app_id ON folders(app_id);
```

---

## Implementation Steps

### Step 1: Database Migration
- Add `folder_id`, `description`, `is_deleted` columns to `files` table
- Create `folders` table
- Create `apps` table
- Write migration script (idempotent — safe to run on existing DB)

### Step 2: Enhanced bridge.py
- Add `upload_to_folder()` method
- Add `list_files(app_id, folder_id, limit, offset)` method
- Add `delete_file(file_unique_id, app_id)` method
- Add `rename_file(file_unique_id, new_name, app_id)` method
- Add `move_file(file_unique_id, folder_id, app_id)` method
- Add `create_folder(name, parent_id, app_id)` method
- Add `list_folders(app_id)` method
- Add `get_app_stats(app_id)` method

### Step 3: Enhanced main.py
- Version all endpoints under `/v1/`
- Add folder CRUD endpoints
- Add file management endpoints
- Add per-app stats endpoint
- Add pagination support
- Add search query parameter to list endpoint
- Add proper error responses with consistent JSON format

### Step 4: Rate Limiting
- Add slowapi or custom middleware
- Rate limit: 100 requests/minute per API key
- Upload rate limit: 10 uploads/minute per API key
- Return 429 with Retry-After header

### Step 5: API Key Management (v1 simple)
- Single key for all apps (current)
- Store in database for future per-app keys
- Add `X-App-Id` header as alternative to query param

### Step 6: Cloudflare Tunnel
- Install cloudflared on VPS
- Create tunnel with public hostname `bridge-api.nhprince.dpdns.org`
- Configure ingress: HTTPS → localhost:9000
- Set up DNS CNAME record

### Step 7: Documentation
- OpenAPI/Swagger docs (auto-generated by FastAPI)
- Integration guide for Cloudflare Workers
- Integration guide for any HTTP client
- Error code reference

### Step 8: Migrate Existing Apps
- Update Prince Snaps `upload.ts` to POST to bridge API
- Update InstaPrince upload flow to use bridge
- Test backward compatibility

---

## Error Response Format

```json
{
  "success": false,
  "error": {
    "code": "FILE_NOT_FOUND",
    "message": "The requested file does not exist or you don't have access"
  }
}
```

### Error Codes

| Code | HTTP Status | Meaning |
|---|---|---|
| `INVALID_API_KEY` | 401 | Missing or wrong API key |
| `FILE_NOT_FOUND` | 404 | File doesn't exist or wrong app_id |
| `FILE_TOO_LARGE` | 413 | Exceeds 2GB limit |
| `EMPTY_FILE` | 400 | Zero-byte upload |
| `INVALID_APP_ID` | 400 | Unknown or missing app_id |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Server-side failure |

---

## Security Considerations

1. **API key in header** (not URL) — prevents logging in access logs
2. **Per-app isolation** — every query includes `app_id` filter
3. **Soft delete** — files marked deleted, not actually removed from Telegram
4. **Rate limiting** — prevents abuse
5. **CORS** — restrict to known origins in production
6. **Cloudflare Tunnel** — hides VPS IP, provides CDN buffering
7. **No bot token exposure** — all Telegram operations happen server-side

---

## Timeline

| Step | Time | Priority |
|---|---|---|
| Step 1: Database migration | 30 min | 🔴 Critical |
| Step 2: Enhanced bridge.py | 2 hours | 🔴 Critical |
| Step 3: Enhanced main.py endpoints | 2 hours | 🔴 Critical |
| Step 4: Rate limiting | 30 min | 🟡 Important |
| Step 5: API key management | 30 min | 🟡 Important |
| Step 6: Cloudflare Tunnel | 1 hour | 🔴 Critical |
| Step 7: Documentation | 1 hour | 🟢 Nice-to-have |
| Step 8: Migrate existing apps | 1 hour | 🟢 After core is done |

**Total: ~8-9 hours for a complete universal storage API**

---

## What This Enables

Any application that can make HTTP requests can now use Telegram as unlimited cloud storage:

- **Cloudflare Workers** → upload user-generated content
- **React/Vue/Angular apps** → file manager UIs
- **Mobile apps** → photo/video backup
- **Scripts/CLI** → file hosting
- **IoT devices** → data upload
- **Third-party integrations** → any service that needs file storage

The API is framework-agnostic, language-agnostic, and platform-agnostic. It just needs HTTP.
