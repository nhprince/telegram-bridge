# Telegram Bridge — MTProto File Storage Service

A universal Telegram bridge that uses MTProto protocol to upload files to Telegram's cloud storage. Supports files up to 2 GB per file (free tier) with zero quality loss and zero disk usage on your server.

## What This Does

Instead of using the standard Telegram Bot API (which has a 50 MB limit and compresses media), this bridge uses the MTProto protocol directly — the same protocol the official Telegram apps use. It uploads files to a private Telegram channel, which acts as unlimited cloud storage.

## Why MTProto?

| Feature | Bot API | MTProto (This Bridge) |
|---------|---------|----------------------|
| Max file size | 50 MB (photos: 10 MB) | **2 GB** (free) / 4 GB (Premium) |
| Quality | Compressed (photos/videos) | **Zero loss** (sent as documents) |
| Disk usage | Stores then uploads | **Zero** (streams directly) |
| Speed | Single connection | **Multiple parallel connections** |
| Media types | Photos, videos, documents | **Any file type** |

## Architecture

```
Your App (Cloudflare Worker)
        ↓ HTTP POST /upload (multipart/form-data)
Telegram Bridge (VPS — This Service)
        ↓ MTProto (encrypted TCP)
Telegram Servers → Your Private Channel
        ↓
   file_id + file_unique_id returned → store in D1/SQLite
```

## How We Built It (Full Process)

### Step 1: Project Setup

```bash
mkdir telegram-bridge && cd telegram-bridge
python3 -m venv venv
source venv/bin/activate
pip install pyrogram tgcrypto fastapi uvicorn python-dotenv aiohttp aiofiles
```

### Step 2: Core Files

**`config.py`** — Loads all credentials from `.env`:
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` — from [my.telegram.org](https://my.telegram.org)
- `BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHANNEL_ID` — your private channel ID
- `API_SECRET_KEY` — custom secret for HTTP API auth

**`bridge.py`** — Pyrogram MTProto client:
- Manages the Telegram bot session
- `upload_file()` — accepts raw bytes, wraps in `BytesIO`, sends via MTProto
- `get_download_url()` — calls Bot API `getFile` to generate download URL
- Auto-detects file type (image → photo, video → video, else → document)

**`main.py`** — FastAPI HTTP server:
- `POST /upload` — accepts multipart file upload, forwards to bridge
- `GET /resolve/{file_unique_id}` — get fresh download URL
- `GET /download/{file_unique_id}` — stream file (hides bot token)
- `GET /stats` — upload statistics
- `GET /health` — health check (no auth)

**`database.py`** — SQLite tracking:
- Tracks every upload with file_id, file_unique_id, app_id, timestamp
- Stores upload counts and download counts

### Step 3: Critical Fixes We Had to Apply

#### Fix 1: Pyrogram Channel ID Limits (Pyrogram 2.0.106 Bug)

**Problem:** Pyrogram 2.0.106 has `MIN_CHANNEL_ID = -1002147483647`. Newer Telegram channels have IDs below this threshold (e.g., `-1004360908345`), causing `ValueError: Peer id invalid`.

**Root Cause:** Telegram expanded their channel ID range, but Pyrogram wasn't updated (see [PR #1430](https://github.com/pyrogram/pyrogram/pull/1430)).

**Solution:** Patch Pyrogram's constants at runtime before any imports:

```python
import pyrogram.utils
pyrogram.utils.MIN_CHANNEL_ID = -1007852516352
pyrogram.utils.MIN_CHAT_ID = -2147483648
```

This is applied at the top of `bridge.py` before importing `pyrogram.Client`.

#### Fix 2: BytesIO `.name` Attribute Missing

**Problem:** Pyrogram's `send_document()` tries to access `document.name` to guess MIME type. Raw `io.BytesIO` objects don't have a `.name` attribute.

**Solution:** Created a helper method `_make_file()` that wraps bytes in BytesIO and sets the `.name`:

```python
def _make_file(self, data: bytes, file_name: str) -> io.BytesIO:
    f = io.BytesIO(data)
    f.name = file_name
    return f
```

#### Fix 3: Extracting file_id from Message Object

**Problem:** `send_document()` returns a `Message` object, not a `File` object. The file attributes are on `result.document`, not directly on `result`.

**Solution:** Check for `document` attribute:

```python
if hasattr(result, 'document') and result.document:
    file_obj = result.document
else:
    file_obj = result
```

#### Fix 4: get_download_url Using MTProto get_file

**Problem:** `client.get_file()` via MTProto sometimes fails with async generator errors.

**Solution:** Use the standard Bot API HTTP endpoint instead:

```python
async def get_download_url(self, file_id: str) -> str:
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
```

#### Fix 5: Environment Variable Name Mismatch

**Problem:** `config.py` was looking for `TELEGRAM_BOT_TOKEN` but `.env` had `BOT_TOKEN`. Also `BRIDGE_SECRET_KEY` vs `API_SECRET_KEY`.

**Solution:** Ensure `.env` variable names match what `config.py` expects.

#### Fix 6: Telegram Rate Limiting (FloodWait)

**Problem:** Multiple rapid auth attempts triggered Telegram's `FLOOD_WAIT_420` — required ~800 seconds wait.

**Solution:** Delete all `.session` files, wait for rate limit to clear, then authenticate once and reuse the session file.

### Step 4: Running the Bridge

```bash
cd ~/telegram-bridge
source venv/bin/activate
python main.py
# → Uvicorn running on http://0.0.0.0:9000
```

### Step 5: Testing

```bash
# Health check
curl http://localhost:9000/health

# Upload a file
echo "test content" > /tmp/test.txt
curl -X POST http://localhost:9000/upload \
  -H "X-API-Key: prince_snaps_bridge_2026" \
  -F "file=@/tmp/test.txt" \
  -F "app_id=bajar-sodai" \
  -F "caption=Test upload"

# Response:
# {
#   "success": true,
#   "file_id": "BQACAgUAAyEGAAMBA-4uOQADC2o2MUYgB-JmkmTVVdHoUnn2Pv9NAALyHgACdAABsFUpGDiIY0GUNh4E",
#   "file_unique_id": "AgAD8h4AAnQAAbBV",
#   "file_name": "test.txt",
#   "file_size": 18,
#   "mime_type": "text/plain",
#   "download_url": "https://api.telegram.org/file/bot.../documents/file_1.bin"
# }
```

## API Reference

### POST /upload
Upload a file to Telegram via MTProto.

| Field | Type | Description |
|-------|------|-------------|
| `file` | File | The file to upload (max 2 GB) |
| `app_id` | String | Application identifier for tracking |
| `caption` | String | Optional caption (max 1024 chars) |
| `channel_id` | Integer | Override default channel (optional) |

**Headers:** `X-API-Key: your-secret-key`

### GET /resolve/{file_unique_id}
Get fresh download URL and metadata for a file.

### GET /download/{file_unique_id}
Stream file directly (hides bot token from end users).

### GET /stats
Get upload statistics (total uploads, total size, total downloads).

### GET /health
Health check — no auth required.

## Configuration

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `TELEGRAM_API_ID` | Yes | API ID from my.telegram.org | — |
| `TELEGRAM_API_HASH` | Yes | API Hash from my.telegram.org | — |
| `BOT_TOKEN` | Yes | Bot token from @BotFather | — |
| `TELEGRAM_CHANNEL_ID` | Yes | Private channel ID (negative, e.g., -1004360908345) | — |
| `BRIDGE_HOST` | No | Bind address | 0.0.0.0 |
| `BRIDGE_PORT` | No | HTTP port | 9000 |
| `API_SECRET_KEY` | Yes | Custom API auth secret | — |
| `MAX_FILE_SIZE` | No | Max upload size | 2147483648 (2 GB) |
| `ALLOWED_ORIGINS` | No | CORS origins | * |
| `DATABASE_PATH` | No | SQLite database path | bridge.db |

## Current Deployment

- **Bot:** @NhStorageapiprincebot (ID: 8932401901)
- **Channel:** Storage (ID: -1004360908345)
- **VPS:** `40.82.136.197`, port 9000
- **Bridge API:** `https://origin-api.nhprince.dpdns.org` (primary), `https://bridge-api.nhprince.dpdns.org` (fallback)
- **API Key:** `prince_snaps_bridge_2026`

### Apps Using the Bridge

| App | `app_id` | D1 Database | Telegram Target | Storage Method | Status |
|-----|----------|-------------|-----------------|----------------|--------|
| **Prince Snaps** | `prince-snaps` | `prince-snaps-db` | Channel `-1004360908345` | MTProto Bridge | ✅ Live |
| **InstaPrince** | N/A | `instaprince-db` | Admin's private chat | Bot API (direct) | ✅ Live |

**Note:** InstaPrince does NOT use the bridge — it uses the Bot API directly. Only Prince Snaps uses the MTProto Bridge. Both apps share the same Telegram bot but target different destinations.

### URL Configuration

| Domain | Cloudflare | Use Case |
|--------|-----------|----------|
| `origin-api.nhprince.dpdns.org` | Grey cloud (DNS-only) | Primary for all uploads and downloads |
| `bridge-api.nhprince.dpdns.org` | Orange cloud (proxied) | Fallback if origin-api unreachable |

**Why origin-api as primary?** Cloudflare's CDN is sometimes unreachable from Bangladesh ISPs. The grey cloud domain connects directly to the VPS.

**Prince Snaps upload flow:**
1. Try `origin-api.nhprince.dpdns.org/v1/upload`
2. If network error → fallback to `bridge-api.nhprince.dpdns.org/v1/upload`

**Prince Snaps download flow:**
1. Load photo list from CF Worker (D1 database)
2. Display photos using `download_url` from bridge (Bot API, works for ≤20MB)
3. For full download → use bridge `GET /v1/download/{file_id}` (MTProto, no size limit)

### App Isolation

Each app using the bridge is isolated via the `app_id` field:

- Files uploaded with `app_id=prince-snaps` are only visible to Prince Snaps
- The `files` table in `bridge.db` stores `app_id` with every upload
- `GET /v1/files` filters by `app_id` — apps cannot see each other's files
- Each app has its own D1 database on Cloudflare
- Each app targets a different Telegram destination (channel vs private chat)

**No data leakage between apps.**

## Deployment

### Systemd Service (Auto-restart on Reboot)

```bash
sudo cp telegram-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bridge
sudo systemctl start telegram-bridge
sudo systemctl status telegram-bridge
```

### Nginx + Cloudflare SSL

The bridge runs behind nginx with Cloudflare Full (Strict) SSL:

- `bridge-api.nhprince.dpdns.org` — Cloudflare proxied (orange cloud), used for uploads ≤100MB and API calls
- `origin-api.nhprince.dpdns.org` — Grey cloud (no Cloudflare proxy), used for large uploads (>100MB) and downloads

**Why two domains?** Cloudflare free plan has a 100MB upload limit. Large files bypass it via the grey cloud subdomain. Downloads also use the grey cloud for maximum speed (no Cloudflare proxy overhead).

### Firewall

```bash
# Only allow Cloudflare IPs to reach port 9000
ufw allow from 173.245.48.0/20 to any port 9000
# ... (all Cloudflare IP ranges)
```

## Known Issues & Fixes

### Cloudflare Connectivity from Bangladesh

**Problem:** Some ISPs in Bangladesh intermittently block or have routing issues with Cloudflare CDN IPs. Users get "Could not connect to bridge-api.nhprince.dpdns.org" errors when uploading or downloading.

**Root Cause:** `bridge-api.nhprince.dpdns.org` routes through Cloudflare's CDN (IPs: 172.67.x.x, 104.21.x.x). When the ISP blocks Cloudflare, all API calls fail.

**Solution:** The bridge provides two domains:
- **`origin-api.nhprince.dpdns.org`** — Grey cloud (DNS-only), resolves directly to VPS IP (`40.82.136.197`). No Cloudflare. Use this as primary.
- **`bridge-api.nhprince.dpdns.org`** — Cloudflare proxied (orange cloud). Used as fallback if origin-api is unreachable.

The bridge-test.html and Prince Snaps app both use `origin-api` as primary with automatic fallback to `bridge-api`.

**User-side fixes if still blocked:**
1. Switch between WiFi and mobile data
2. Use a VPN (any free VPN works)
3. Try a different browser
4. Clear browser cache and DNS cache

### Upload: Cloudflare 100MB Limit Bypass

**Problem:** Cloudflare free plan has a hard 100MB upload limit at the CDN edge. Files >100MB sent through `bridge-api.nhprince.dpdns.org` get HTTP 413.

**Solution:** For files >100MB, use `origin-api.nhprince.dpdns.org` (grey cloud, DNS-only) which connects directly to the VPS origin, bypassing Cloudflare entirely. The bridge-test.html automatically switches based on file size.

### index.html Overwrite

**Problem:** The `out/index.html` was accidentally overwritten by `bridge-test.html` during manual copy operations. This caused the deployed site to show the test page instead of the actual Prince Snaps app.

**Solution:** Always rebuild with `vite build` (which uses `emptyOutDir: true`). The `bridge-test.html` should be copied to `out/` as a separate file, never as `index.html`.

### Cloudflare 100MB Upload Limit

**Problem:** Cloudflare free plan has a hard 100MB upload limit at the CDN edge. Files >100MB get HTTP 413.

**Solution:** Use `origin-api.nhprince.dpdns.org` (grey cloud, proxied:false) for uploads >100MB. This bypasses Cloudflare entirely and goes directly to the VPS.

### Bot API getFile ~20MB Limit

**Problem:** The Telegram Bot API `getFile` endpoint returns "Bad Request: file is too big" for files >20MB.

**Solution:** The bridge handles this gracefully by returning an empty `download_url`. For actual file downloads, use the `GET /v1/download/{file_unique_id}` endpoint which streams via MTProto (no size limit).

### Telegram MTProto upload.getFile Limit Requirement

**Problem:** When streaming downloads via raw MTProto `upload.getFile`, the `limit` parameter must be a **power-of-2 multiple of 4096** (i.e., 1×4096, 2×4096, 4×4096, 8×4096, 16×4096, 32×4096, 64×4096, 128×4096, 256×4096). Non-power-of-2 multiples like 3×4096, 5×4096, 23×4096 FAIL with `LimitInvalid`.

**Solution:** The download endpoint rounds up the chunk size to the next power-of-2 × 4096. The `offset + limit` can exceed the file size — Telegram returns fewer bytes than requested for the last chunk.

### access_hash Not in send_document Response

**Problem:** Pyrogram's high-level `send_document()` returns a `Message` object, but the `Document` inside it doesn't include `access_hash` or `dc_id` — these are needed for MTProto downloads.

**Solution:** After upload, call the raw `channels.GetMessages` API to get the full document metadata including `access_hash`, `dc_id`, `file_reference`, and numeric `media_id`. Retry up to 3 times with 1s delay (message may not be immediately available).

### file_id vs media_id

**Problem:** The Telegram `file_id` string (e.g., `BQACAgUAAyEGAAMBA-4uOQAD...`) is NOT the same as the numeric MTProto `media_id` (e.g., `6178968208062554629`). `InputDocumentFileLocation.id` requires the numeric ID.

**Solution:** Store both `file_id` (string) and `media_id` (integer) in the database.

### Binary Fields in JSON Response

**Problem:** The `file_reference` column in SQLite is a BLOB (binary data). When FastAPI tries to serialize it to JSON, it fails with `UnicodeDecodeError`.

**Solution:** Strip `file_reference`, `access_hash`, `media_id`, `dc_id` from list responses. These are internal fields not needed by the frontend.

### CORS on Error Responses

**Problem:** FastAPI's CORS middleware only adds headers to successful responses. Error responses (422, 429, 500) from early returns (rate limiter, auth) bypass CORS.

**Solution:** Added global exception handlers for `HTTPException`, `RequestValidationError`, and `Exception` that include CORS headers. Also added CORS headers to the rate limiter's 429 responses.

### Large File Download Browser Freeze

**Problem:** Using `fetch()` → `res.blob()` → `URL.createObjectURL()` loads the entire file into browser memory. For 170MB+ files on low-end devices, this freezes the browser and triggers "unresponsive" warnings.

**Solution:** Two-tier approach in `bridge-test.html`:
1. **Primary (Chrome/Edge):** Uses the File System Access API (`showSaveFilePicker` + `createWritable`) to stream chunks directly to disk as they arrive. Memory usage stays flat at ~1MB regardless of file size.
2. **Fallback (Firefox/Safari/mobile):** Accumulates chunks in a blob and triggers download via `<a download>`. Works on all browsers but uses more memory for large files.

**Important:** Downloads use `fetch()` with the `X-API-Key` header — the old `<a href="download_url">` approach does NOT send the API key and will be rejected by the bridge.

All downloads use `origin-api.nhprince.dpdns.org` (grey cloud, no Cloudflare) for direct high-speed streaming — measured at 8+ MB/s.

### Upload Fails at 100% — "Bad Request" After Bridge Upload (Prince Snaps)

**Problem:** After uploading a file to the bridge (progress reaches 100%), the frontend throws a "Bad Request" error. The file is successfully uploaded to Telegram, but the metadata save to D1 fails.

**Root Cause:** The `UploadResponse` Pydantic model in `main.py` was missing the `file_id` field. The bridge's internal `upload_file()` function returns `file_id`, but the API response model only included `file_unique_id`. The frontend reads `bridgeResult.file_id` which was `undefined`, then sends it to the Worker's D1 insert. The Worker checks `if (!body.file_id || !body.file_unique_id)` — since `file_id` was `undefined` (falsy), it returns HTTP 400 "Missing bridge upload data".

**Fix:** Added `file_id: str` to the `UploadResponse` model and included `file_id=result["file_id"]` in the return statement of the `/v1/upload` endpoint.

**Files changed:** `main.py` — `UploadResponse` class and `return UploadResponse(...)` in `upload_file()` endpoint.

### Venv Corruption After Python Upgrade

**Problem:** After `unattended-upgrades` upgrades Python (e.g., 3.11 → 3.12), the bridge's virtualenv breaks. The `python` binary inside `venv/bin/` becomes a symlink to the old Python version which no longer exists. Systemd fails with "No such file or directory" even though `venv/bin/` looks intact.

**Symptoms:** `systemctl status telegram-bridge` shows `Failed to execute .../venv/bin/gunicorn: No such file or directory`. Running `venv/bin/python --version` gives "No such file or directory".

**Diagnosis:** `ls -la venv/bin/python*` shows broken symlinks pointing to the old Python version.

**Solution:** Recreate the venv from scratch:
```bash
cd ~/telegram-bridge
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn  # if using gunicorn
sudo systemctl restart telegram-bridge
```

**Note:** If using `uvicorn` instead of `gunicorn` (installed by default with `pip install uvicorn`), you don't need a separate gunicorn install. Update the systemd service to use:
```
ExecStart=/home/nhprince/telegram-bridge/venv/bin/uvicorn main:app --host 0.0.0.0 --port 9000 --workers 1 --timeout-keep-alive 5400 --log-level warning
```

## Increasing the File Size Limit (Future Reference)

> **Current limit: 500MB.** This can be increased when you get a more powerful VPS. Everything below documents exactly what to change and why.

### Current Limits at Every Layer

| Layer | Current Setting | Hard Limit | Notes |
|-------|----------------|------------|-------|
| **Telegram MTProto** | 500MB | **2GB** (free) / 4GB (Premium) | Telegram server-side limit |
| **FastAPI `file.read()`** | Loads entire file into RAM | **~300MB safe** (VPS has 842MB RAM, 293MB free) | **Biggest blocker** — see below |
| **Gunicorn timeout** | 5400s (90 min) | Configurable | 500MB takes ~55 min at 0.15 MB/s |
| **nginx `client_max_body_size`** (bridge-api) | 500M | Configurable | Must match or exceed MAX_FILE_SIZE |
| **nginx `client_max_body_size`** (origin-api) | 2G | Configurable | Already set to 2GB |
| **VPS disk space** | 330MB free | 29GB total | Temp buffering needs free space |
| **Cloudflare free plan** | 100MB upload | 100MB | Already bypassed via grey cloud origin-api |

### What Needs to Change for 1GB

1. **`config.py`** — Change `MAX_FILE_SIZE` to `1073741824` (1024³ = 1GB)
2. **`bridge-api` nginx vhost** — Change `client_max_body_size` to `1024M`
3. **`gunicorn.conf.py`** — Change `timeout` to `7200` (2 hours)
4. **`main.py`** — **CRITICAL:** Replace `file_data = await file.read()` with streaming upload (see below)
5. **VPS disk** — Free up at least 1-2GB for temp buffering

### What Needs to Change for 2GB (Maximum)

All of the above, plus:
- Gunicorn `timeout` → `10800` (3 hours) — 2GB at 0.15 MB/s ≈ 220 min
- VPS disk — At least 3-4GB free for temp buffering
- **Streaming upload is MANDATORY** at this size

### The Critical Fix: Streaming Upload

**Current code in `main.py` (line ~297):**
```python
file_data = await file.read()  # Loads ENTIRE file into RAM
```

This loads the complete file into Python memory before uploading. On the current VPS (842MB RAM, ~293MB available):
- 500MB file → works (barely)
- 1GB file → **OOM crash**
- 2GB file → **definitely crashes**

**Required fix — Stream to temp file instead:**
```python
import tempfile, aiofiles

# Instead of: file_data = await file.read()
# Stream to disk:
with tempfile.NamedTemporaryFile(delete=False, dir="/tmp/telegram-bridge-uploads") as tmp:
    async for chunk in file.file:
        tmp.write(chunk)
    tmp_path = tmp.name

# Then pass file path to Pyrogram instead of bytes
# Modify bridge.upload_file() to accept file_path parameter
# Pyrogram can stream from file path directly
```

This way only a small buffer is in RAM at any time, regardless of file size.

### Step-by-Step Upgrade Checklist

When you get a new VPS:

1. [ ] Set `MAX_FILE_SIZE` in `config.py` to desired limit
2. [ ] Update `client_max_body_size` in `bridge-api` nginx config
3. [ ] Update gunicorn `timeout` in `gunicorn.conf.py`
4. [ ] **Implement streaming upload** in `main.py` (replace `file.read()` with temp file streaming)
5. [ ] **Update `bridge.py`** `upload_file()` to accept file path instead of bytes
6. [ ] Ensure VPS has enough free disk (at least 2× max file size for temp space)
7. [ ] Ensure VPS has enough RAM (at least 2GB recommended for 1GB+ files)
8. [ ] Restart nginx: `sudo systemctl reload nginx`
9. [ ] Restart bridge: `sudo systemctl restart telegram-bridge`
10. [ ] Test with a file larger than 500MB

### New VPS Recommendations

For comfortable 2GB file support:
- **RAM:** 4GB+ (so streaming buffers don't cause OOM)
- **Disk:** 40GB+ SSD (for temp buffering during upload)
- **Bandwidth:** 1TB+ monthly (large file transfers eat bandwidth)
- **CPU:** 2 vCPUs (MTProto encryption is CPU-bound)

## VPS Cleanup (2026-06-22)

The VPS was cleaned up to free disk space. All project repos were synced to GitHub and deleted from local disk. Only `telegram-bridge`, `prince-snaps`, and `instaprince` remain.

**Disk before:** 99% full (328M free)  
**Disk after:** 51% full (14G free)

Full cleanup log: `/home/nhprince/VPS-CLEANUP-LOG.md` (on VPS)  
All deleted repos can be restored with `git clone` from GitHub.

## License

MIT

---

## Appendix A: CORS Fix (2026-06-25)

### Problem

Cross-origin `fetch()` / `XMLHttpRequest` from browser to the bridge API was blocked:

```
Access to XMLHttpRequest at 'https://origin-api.nhprince.dpdns.org/v1/stats'
from origin 'https://bridge-diag.pages.dev' has been blocked by CORS policy:
Response to preflight doesn't pass access control check: No
'Access-Control-Allow-Origin' header is present on the requested resource.
```

Despite everything working from `curl` and localhost, the browser refused the request.

### Investigation (3 hours, 5 different failed approaches)

**Attempt 1**: Adding `add_header 'Access-Control-Allow-Origin' '*'` at the nginx `server` level.
- **Failed**: `add_header` is forbidden inside `if` blocks at server level in nginx.

**Attempt 2**: Adding `add_header ... always` inside `location` blocks + letting FastAPI CORSMiddleware handle it.
- **Failed**: Through Cloudflare (orange cloud), this created **DUPLICATE CORS headers** — both FastAPI and nginx added `access-control-allow-origin`. Browsers reject responses with duplicate CORS headers.

**Attempt 3**: Handling OPTIONS preflight by returning 204 at nginx using `add_header` inside `location` + `if` blocks.
- **Partially worked**: This actually works (`add_header` IS allowed inside `location` + `if`, just NOT at server level). But the duplicate headers issue remained for bridge-api through Cloudflare.

**Attempt 4**: Removing FastAPI CORSMiddleware entirely, handling all CORS in nginx.
- **Failed**: Couldn't reliably handle OPTIONS for POST-only routes (`/v1/upload`) without duplicating headers on GET routes.

**Attempt 5** (final): Let FastAPI handle CORS via CORSMiddleware (ordered FIRST), add nginx-level OPTIONS handling ONLY for `/v1/upload` (POST-only route) using `location` + `if` block.

### Root Causes (3 issues, all had to be fixed)

**Issue 1: FastAPI middleware ordering**

In Starlette/FastAPI, `add_middleware` prepends to the stack. The FIRST middleware added becomes the outermost wrapper. CORSMiddleware must be added BEFORE any other middleware, or OPTIONS preflight gets intercepted by rate limiting or auth before CORS handles it.

```python
# ✅ CORRECT — CORSMiddleware is outermost, intercepts OPTIONS first
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
app.add_middleware(RateLimitMiddleware, ...)

# ❌ WRONG — RateLimitMiddleware runs first, blocks OPTIONS with 429
app.add_middleware(RateLimitMiddleware, ...)
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
```

**Issue 2: CORS duplication through Cloudflare proxy**

When using orange cloud (proxied), Cloudflare passes through ALL origin headers. If nginx ALSO adds CORS headers via `add_header`, the browser receives DUPLICATE headers and rejects the preflight:
```
access-control-allow-origin: https://bridge-diag.pages.dev    ← from FastAPI
Access-Control-Allow-Origin: *                                 ← from nginx
```

**Fix**: Never add CORS headers at nginx level. Let FastAPI CORSMiddleware be the single source of truth.

**Issue 3: OPTIONS preflight on POST-only routes**

FastAPI routes declared with `@app.post("/v1/upload")` do not respond to OPTIONS unless CORSMiddleware intercepts it BEFORE route matching. But even with CORSMiddleware working, the route-specific middleware stack doesn't include an OPTIONS handler. The solution is to handle OPTIONS directly in nginx for that specific route.

### Fix Applied

**`main.py`** — Ensure CORSMiddleware is FIRST middleware:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
# app.add_middleware(RateLimitMiddleware, ...) goes AFTER, not before
```

**`origin-api.nhprince.dpdns.org.conf`** and **`bridge-api.nhprince.dpdns.org.conf`** — Handle OPTIONS for `/v1/upload` in nginx location:
```nginx
location /v1/upload {
    # Handle OPTIONS preflight directly — return 204 with CORS headers
    if ($request_method = OPTIONS) {
        add_header 'Access-Control-Allow-Origin' '*' always;
        add_header 'Access-Control-Allow-Methods' 'POST, OPTIONS' always;
        add_header 'Access-Control-Allow-Headers' 'X-API-Key, Content-Type' always;
        add_header 'Access-Control-Max-Age' 86400 always;
        return 204;
    }

    # Everything else → proxy to FastAPI
    client_body_timeout 5400s;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_pass http://telegram_bridge;
    proxy_set_header Host $host;
}
```

**✅ WHY THIS WORKS**: `add_header` inside `location` + `if` IS allowed in nginx (unlike inside `server` + `if`). This returns 204 with CORS headers for the preflight, while proxying the actual POST to FastAPI. Since this only handles OPTIONS (not all responses), there are no duplicate headers.

### Nginx `add_header` Rules to Remember

| Location | `add_header` allowed? | Notes |
|----------|----------------------|-------|
| `server` block (directly) | ✅ Yes | For non-conditional headers |
| `server` block inside `if` | ❌ **No** | nginx forbids this |
| `location` block | ✅ Yes | Most common placement |
| `location` block inside `if` | ✅ Yes | Required for conditional CORS |

### Verification

After applying the fix:
```bash
# Test OPTIONS preflight through nginx
curl -X OPTIONS \
  -H "Origin: https://bridge-diag.pages.dev" \
  -H "Access-Control-Request-Method: POST" \
  "https://origin-api.nhprince.dpdns.org/v1/upload" -v

# Expected: 204 No Content with headers:
# Access-Control-Allow-Origin: *
# Access-Control-Allow-Methods: POST, OPTIONS
# Access-Control-Allow-Headers: X-API-Key, Content-Type
```

### Test Page

Updated `bridge-diag` test page uses `XMLHttpRequest` instead of `fetch()` for cross-origin requests — more reliable across network conditions and doesn't trigger ISP interception patterns as.

Deployed to: `https://bridge-diag.pages.dev`

---

## Appendix B: Complete Rebuild Guide (A-Z From Scratch)

> **Use this if you lose access to the VPS or need to rebuild everything.** This is the legitimate step-by-step process.

### Prerequisites

- A VPS with at least 2GB RAM and 20GB disk (Ubuntu 22.04+ recommended)
- A domain on Cloudflare (free DDNS or any domain)
- Telegram bot token from [@BotFather](https://t.me/botfather)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org/apps)
- A private Telegram channel (the storage destination)

### Phase 1: Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install all dependencies
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

### Phase 2: Application Code

```bash
# Clone or create the project
cd ~
git clone https://github.com/nhprince/telegram-bridge.git  # OR create from scratch
cd telegram-bridge

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**`requirements.txt`**:
```
pyrogram==2.0.106
tgcrypto
fastapi
uvicorn[standard]
python-dotenv
aiohttp
aiofiles
```

### Phase 3: Configuration

Create `.env`:
```bash
cat > .env << 'EOF'
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abc123def...
BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHANNEL_ID=-1004360908345
BRIDGE_HOST=0.0.0.0
BRIDGE_PORT=9000
API_SECRET_KEY=your-secret-key-here
ALLOWED_ORIGINS=*
DATABASE_PATH=bridge.db
EOF
chmod 600 .env
```

Verify config loads:
```bash
source venv/bin/activate
python -c "from config import API_SECRET_KEY; print('Config OK:', bool(API_SECRET_KEY))"
```

### Phase 4: Python Path Fix (Pyrogram Channel ID Bug)

If your Telegram channel ID is newer (e.g., `-1004360908345`), Pyrogram 2.0.106 may crash with `ValueError: Peer id invalid`. Fix this at the TOP of `bridge.py` before any Pyrogram imports:

```python
import pyrogram.utils
pyrogram.utils.MIN_CHANNEL_ID = -1007852516352
pyrogram.utils.MIN_CHAT_ID = -2147483648
```

### Phase 5: Test the Application

```bash
source venv/bin/activate
python main.py
# Should start on port 9000
# Test in another terminal:
curl http://127.0.0.1:9000/health
```

### Phase 6: Systemd Service

```bash
sudo tee /etc/systemd/system/telegram-bridge.service << 'EOF'
[Unit]
Description=Telegram Bridge API
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/root/telegram-bridge
ExecStartPre=/usr/bin/mkdir -p /var/log/telegram-bridge /tmp/telegram-bridge-uploads
ExecStart=/root/telegram-bridge/venv/bin/uvicorn main:app --host 0.0.0.0 --port 9000 --workers 1 --timeout-keep-alive 5400 --log-level warning
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable telegram-bridge
sudo systemctl start telegram-bridge

# Verify it's running
sudo systemctl status telegram-bridge
# Test through systemd service
curl http://127.0.0.1:9000/health
```

### Phase 7: SSL Certificates

```bash
# Create webroot for ACME challenge
sudo mkdir -p /var/www/certbot

# Get SSL certificates for both domains
sudo certbot --nginx -d origin-api.YOURDOMAIN -d bridge-api.YOURDOMAIN

# Test auto-renewal
sudo certbot renew --dry-run
```

### Phase 8: Nginx Configuration

**Step 8a**: Create the upstream block at `/etc/nginx/conf.d/bridge-upstream.conf`:
```bash
sudo tee /etc/nginx/conf.d/bridge-upstream.conf << 'EOF'
upstream telegram_bridge {
    server 127.0.0.1:9000;
    keepalive 32;
}
EOF
```

**Step 8b**: Create the **origin-api** domain (grey cloud, direct).

⚠️ Replace `origin-api.YOURDOMAIN` and `YOUR_VPS_IP` with actual values.

```bash
sudo tee /etc/nginx/conf.d/origin-api.YOURDOMAIN.conf << 'NGINX'
# HTTP → HTTPS redirect
server {
    listen 80;
    server_name origin-api.YOURDOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

# HTTPS — Grey Cloud (direct to VPS)
server {
    listen 443 ssl http2;
    server_name origin-api.YOURDOMAIN;

    ssl_certificate /etc/letsencrypt/live/origin-api.YOURDOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/origin-api.YOURDOMAIN/privkey.pem;

    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    client_max_body_size 500M;
    client_body_timeout 5400s;
    proxy_connect_timeout 60s;
    proxy_send_timeout 5400s;
    proxy_read_timeout 5400s;

    gzip on;
    gzip_min_length 1000;
    gzip_types application/json text/plain;

    # POST-only routes need explicit OPTIONS handling
    # (location + if allows add_header, server + if does NOT)
    location /v1/upload {
        if ($request_method = OPTIONS) {
            add_header 'Access-Control-Allow-Origin' '*' always;
            add_header 'Access-Control-Allow-Methods' 'POST, OPTIONS' always;
            add_header 'Access-Control-Allow-Headers' 'X-API-Key, Content-Type' always;
            add_header 'Access-Control-Max-Age' 86400 always;
            return 204;
        }

        client_body_timeout 5400s;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_pass http://telegram_bridge;
        proxy_set_header Host $host;
    }

    # All other routes → FastAPI (CORSMiddleware handles CORS)
    location / {
        proxy_pass http://telegram_bridge;
        proxy_set_header Host $host;
    }

    location /docs { proxy_pass http://telegram_bridge; }
    location = /openapi.json { proxy_pass http://telegram_bridge; }
    location ~ /\\. { deny all; }
}
NGINX
```

**Step 8c**: Create the **bridge-api** domain (orange cloud, proxied through CF).
Replace `bridge-api.YOURDOMAIN`:

```bash
sudo tee /etc/nginx/conf.d/bridge-api.YOURDOMAIN.conf << 'NGINX'
server {
    listen 80;
    server_name bridge-api.YOURDOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2;
    server_name bridge-api.YOURDOMAIN;

    ssl_certificate /etc/letsencrypt/live/bridge-api.YOURDOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bridge-api.YOURDOMAIN/privkey.pem;

    # Real IP from Cloudflare
    set_real_ip_from 173.245.48.0/20;
    set_real_ip_from 103.21.244.0/22;
    set_real_ip_from 103.22.200.0/22;
    set_real_ip_from 103.31.4.0/22;
    set_real_ip_from 108.162.192.0/18;
    set_real_ip_from 141.101.64.0/18;
    set_real_ip_from 162.158.0.0/15;
    set_real_ip_from 172.67.0.0/16;
    set_real_ip_from 188.114.96.0/20;
    set_real_ip_from 190.93.240.0/20;
    set_real_ip_from 197.234.240.0/22;
    set_real_ip_from 198.41.128.0/17;
    set_real_ip_from 2400:cb00::/32;
    set_realip_from 2606:4700::/32;
    set_real_ip_from 2803:f800::/32;
    set_real_ip_from 2405:b500::/32;
    set_real_ip_from 2405:8100::/32;
    real_ip_header CF-Connecting-IP;

    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Content-Type-Options nosniff always;

    client_max_body_size 100M;
    client_body_timeout 120s;
    proxy_connect_timeout 60s;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;

    gzip on;
    gzip_min_length 1000;
    gzip_types application/json text/plain;

    location /v1/upload {
        if ($request_method = OPTIONS) {
            add_header 'Access-Control-Allow-Origin' '*' always;
            add_header 'Access-Control-Allow-Methods' 'POST, OPTIONS' always;
            add_header 'Access-Control-Allow-Headers' 'X-API-Key, Content-Type' always;
            return 204;
        }
        client_body_timeout 120s;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_pass http://telegram_bridge;
        proxy_set_header Host $host;
        proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
    }

    location / {
        proxy_pass http://telegram_bridge;
        proxy_set_header Host $host;
        proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
    }

    location /docs { proxy_pass http://telegram_bridge; }
    location = /openapi.json { proxy_pass http://telegram_bridge; }
    location ~ /\\. { deny all; }
}
NGINX
```

**Step 8d**: Reload nginx:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

### Phase 9: Cloudflare DNS Setup

In your Cloudflare dashboard:

| Type | Name | Content | Proxy Status |
|------|------|---------|--------------|
| A | origin-api | YOUR_VPS_IP | **DNS only** (grey cloud ☁️) |
| A | bridge-api | YOUR_VPS_IP | **Proxied** (orange cloud �) |

**Grey cloud** = DNS points directly to VPS. No CDN, no proxy, no 100MB limit.
**Orange cloud** = Traffic goes through Cloudflare CDN. 100MB upload limit applies.

### Phase 10: Firewall

```bash
# SSH
sudo ufw allow 22/tcp

# HTTP/HTTPS (from anywhere — Cloudflare and clients need this)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable
sudo ufw enable
```

**Do NOT open port 9000 to the public.** Port 9000 is only accessed by nginx locally (`127.0.0.1:9000`). All external traffic comes through 443 → nginx → 9000.

### Phase 11: Testing

```bash
# Local test
curl http://127.0.0.1:9000/health

# Through origin-api (grey cloud)
curl https://origin-api.YOURDOMAIN/health
curl https://origin-api.YOURDOMAIN/v1/stats -H "X-API-Key: your-secret"

# Through bridge-api (orange cloud / CF proxy)
curl https://bridge-api.YOURDOMAIN/health
curl https://bridge-api.YOURDOMAIN/v1/stats -H "X-API-Key: your-secret"

# CORS preflight test (critical!)
curl -X OPTIONS \
  -H "Origin: https://your-app.pages.dev" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: X-API-Key,Content-Type" \
  -v https://origin-api.YOURDOMAIN/v1/stats | grep -i access-control

# Upload test
echo "test content" > /tmp/upload_test.txt
curl -X POST https://origin-api.YOURDOMAIN/v1/upload \
  -H "X-API-Key: your-secret" \
  -F "file=@/tmp/upload_test.txt" \
  -F "app_id=test"
```

### Phase 12: Deploy a Test Page

```bash
cd ~/bridge-diag  # or any test HTML directory
wrangler pages deploy . --project-name bridge-diag
```

Test page should:
- Use `XMLHttpRequest` (not `fetch()`) for cross-origin requests
- Try `origin-api` first, fallback to `bridge-api` on error
- Send proper `FormData` with field name `file` (not raw blob)
- Use 30-second timeout for slow connections

### Quick Reference: Nginx Config Checksum

After setup, run:
```bash
sudo nginx -t && echo "✅ Nginx config OK"
sudo systemctl is-active telegram-bridge && echo "✅ Bridge running"
sudo systemctl is-active nginx && echo "✅ Nginx running"
```

### Troubleshooting Quick Fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| 502 Bad Gateway | Bridge not running or wrong port | `sudo systemctl restart telegram-bridge` |
| 405 Method Not Allowed | OPTIONS hitting POST-only route | Add `if ($request_method = OPTIONS)` block in nginx location |
| CORS error in browser | Duplicate headers or missing headers | Check: is CORSMiddleware FIRST middleware? Is nginx adding duplicate CORS? |
| "Could not connect" from BD ISP | Cloudflare blocked | Use origin-api (grey cloud) instead of bridge-api |
| 413 Request Entity Too Large | Cloudflare 100MB limit | Use origin-api (grey cloud) for uploads >100MB |
| Slow uploads from BD | ISP throttling | Normal for BD; use grey cloud, not CF proxy |
| `venv` broken after Python upgrade | Broken symlinks | Recreate venv: `rm -rf venv && python3 -m venv venv && pip install -r requirements.txt` |
