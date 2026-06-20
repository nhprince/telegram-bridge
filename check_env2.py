#!/usr/bin/env python3
"""Check .env file content in bytes."""
with open('.env', 'rb') as f:
    content = f.read()
print(f'File size: {len(content)} bytes')
lines = content.split(b'\n')
print(f'Lines: {len(lines)}')
for line in lines:
    if b'BOT' in line:
        print(f'BOT line ({len(line)} bytes): {line}')
