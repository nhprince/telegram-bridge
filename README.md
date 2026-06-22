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
- **Apps using it:** Prince Snaps (live at prince-snaps.pages.dev), BajarSodai (planned)
- **API Key:** `prince_snaps_bridge_2026`

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

## License

MIT
