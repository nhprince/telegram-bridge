# Telegram Bridge — MTProto File Storage Service

A universal Telegram bridge that uses MTProto protocol to upload files to Telegram's cloud storage. Supports files up to 2 GB per file (free tier) with zero quality loss and zero disk usage on your server.

## Features

- **2 GB per file** (vs 10 MB with standard Bot API)
- **Zero quality loss** — files uploaded as documents, no compression
- **Zero disk usage** — files stream directly to Telegram, never stored locally
- **Multi-app support** — one bridge serves all your applications
- **Token hidden** — bot token never exposed to end users
- **Download proxy** — files stream through your bridge, hiding Telegram bot token
- **Database tracking** — SQLite database tracks all uploads per app
- **REST API** — simple HTTP API callable from any language

## Architecture

```
Your App (Cloudflare Worker)
        ↓ HTTP POST /upload
Telegram Bridge (This Service)
        ↓ MTProto
Telegram Servers (Your Private Channel)
        ↓
   file_id returned → stored in your D1/SQLite
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run the Bridge

```bash
python main.py
```

The API will be available at `http://0.0.0.0:9000`

### 4. Test

```bash
curl -X POST http://localhost:9000/upload \
  -H "X-API-Key: your-secret-key" \
  -F "file=@/path/to/photo.jpg" \
  -F "app_id=prince-snaps"
```

## API Endpoints

### POST /upload
Upload a file to Telegram.

**Headers:**
- `X-API-Key`: Your API secret key

**Form Data:**
- `file`: The file to upload (max 2 GB)
- `app_id`: Application identifier (e.g., "prince-snaps")
- `caption`: Optional caption
- `channel_id`: Override default channel (optional)

**Response:**
```json
{
  "success": true,
  "file_id": "ACADBAADwAADIhECEBOKflkbEmoFAg",
  "file_unique_id": "AgADBQADr6cxG...",
  "file_name": "photo.jpg",
  "file_size": 5242880,
  "mime_type": "image/jpeg",
  "download_url": "https://api.telegram.org/file/bot.../photo.jpg"
}
```

### GET /resolve/{file_unique_id}
Resolve a file_unique_id to get a fresh download URL and metadata.

### GET /download/{file_unique_id}
Stream a file directly (hides bot token from end users).

### GET /stats
Get upload statistics.

### GET /health
Health check (no auth required).

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `TELEGRAM_API_ID` | API ID from my.telegram.org | — |
| `TELEGRAM_API_HASH` | API Hash from my.telegram.org | — |
| `BOT_TOKEN` | Storage bot token from @BotFather | — |
| `TELEGRAM_CHANNEL_ID` | Private channel ID (negative) | — |
| `BRIDGE_PORT` | HTTP API port | 9000 |
| `API_SECRET_KEY` | Secret key for API auth | change-me |
| `MAX_FILE_SIZE` | Max upload size in bytes | 2147483648 (2 GB) |

## Deployment

### On a VPS (Ubuntu 22.04+)

```bash
# Clone
git clone https://github.com/nhprince/telegram-bridge.git
cd telegram-bridge

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Add your credentials

# Run with systemd (auto-restart on reboot)
sudo cp telegram-bridge.service /etc/systemd/system/
sudo systemctl enable telegram-bridge
sudo systemctl start telegram-bridge
```

### Systemd Service File

```ini
[Unit]
Description=Telegram Bridge Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/telegram-bridge
ExecStart=/root/telegram-bridge/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PATH=/root/telegram-bridge/venv/bin:/usr/local/bin:/usr/bin

[Install]
WantedBy=multi-user.target
```

## Using from Cloudflare Workers

```typescript
// Upload a file
const formData = new FormData();
formData.append('file', file);
formData.append('app_id', 'prince-snaps');

const response = await fetch('http://your-vps-ip:9000/upload', {
  method: 'POST',
  headers: {
    'X-API-Key': 'your-secret-key',
  },
  body: formData,
});

const result = await response.json();
// Store result.file_id and result.file_unique_id in D1
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

Each app uses a different `app_id` for tracking. You can also use different `channel_id` per app to organize files in separate private channels.

## Security

1. **API Key authentication** — all endpoints require `X-API-Key` header
2. **Bot token hidden** — download URLs are proxied through the bridge
3. **No disk storage** — files are streamed, never written to disk
4. **Firewall** — only allow port 9000 from Cloudflare IPs

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `SESSION_PASSWORD_NEEDED` | Delete the `telegram-bridge.session` file and restart |
| `FILE_REFERENCE_EXPIRED` | Re-upload the file to get new file_id |
| `FLOOD_WAIT` | Telegram rate limit — wait and retry |
| Upload fails for large file | Check `MAX_FILE_SIZE` and VPS memory |

## License

MIT
