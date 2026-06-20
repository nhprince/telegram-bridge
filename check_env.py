#!/usr/bin/env python3
"""Check what's in the .env file."""
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line.startswith('BOT_TOKEN'):
            print(f'BOT_TOKEN line: {repr(line)}')
            token = line.split('=', 1)[1]
            print(f'Token length: {len(token)}')
            print(f'Token ends with: ...{token[-10:]}')
