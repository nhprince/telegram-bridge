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

## Deployment

### Systemd Service (Auto-restart on Reboot)

```bash
sudo cp telegram-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bridge
sudo systemctl start telegram-bridge
sudo systemctl status telegram-bridge
```

### Firewall

```bash
# Only allow Cloudflare Workers to reach port 9000
ufw allow from 173.245.48.0/20 to any port 9000
ufw allow from 103.21.244.0/22 to any port 9000
# ... (all Cloudflare IP ranges)
```

## Using from Cloudflare Workers

```typescript
const formData = new FormData();
formData.append('file', fileBlob);
formData.append('app_id', 'bajar-sodai');

const response = await fetch('http://your-vps-ip:9000/upload', {
  method: 'POST',
  headers: { 'X-API-Key': 'your-secret-key' },
  body: formData,
});

const result = await response.json();
// Store result.file_id and result.file_unique_id in D1
// Use result.download_url for displaying the file
```

## Multi-App Architecture

One bridge serves all your applications:

```
                    ┌─ Prince Snaps (app_id: prince-snaps)
                    │
Telegram Bridge ────┼─ BajarSodai (app_id: bajar-sodai)
                    │
                    └─ Future App (app_id: anything)
```

Each app uses a different `app_id` for tracking. You can optionally use different `channel_id` per app.

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| `Peer id invalid: -100XXXX` | Pyrogram channel ID limit | Apply the `MIN_CHANNEL_ID` patch (automatic in this repo) |
| `ChatWriteForbidden` | Bot lacks Post Messages permission | Grant the bot admin rights with Post Messages in the channel |
| `FLOOD_WAIT_420` | Too many auth attempts | Delete `.session` files, wait ~15 min, restart |
| `AccessTokenInvalid` | Wrong bot token | Double-check token from @BotFather |
| `ModuleNotFoundError: aiohttp` | Missing dependency | `pip install aiohttp` |
| `'_io.BytesIO' object has no attribute 'name'` | Missing .name on BytesIO | Fixed in `_make_file()` helper |
| `CHANNEL_INVALID` | Bot not in channel | Add bot as admin, send a message to the channel |

## Security Notes

1. **Bot token never exposed** — download URLs are generated server-side
2. **API Key authentication** — all endpoints except `/health` require `X-API-Key`
3. **No disk storage** — files are streamed directly to Telegram
4. **Session file** — contains Telegram session data; keep it secure (in `.gitignore`)
5. **Firewall** — restrict port 9000 to Cloudflare IPs only

## Current Deployment

- **Bot:** @NhStorageapiprincebot (ID: 8932401901)
- **Channel:** Storage (ID: -1004360908345)
- **VPS:** Running on port 9000
- **Apps using it:** Prince Snaps, BajarSodai (planned)

## License

MIT
