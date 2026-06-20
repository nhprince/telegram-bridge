#!/usr/bin/env python3
"""Write the .env file with actual credentials."""
import os

# Read the actual .env to see what's there
with open('.env', 'r') as f:
    current = f.read()

print(f"Current .env content ({len(current)} bytes):")
print(current)
print("---")

# The issue is BOT_TOKEN is empty - it was truncated during write
# Let's fix it by writing directly
lines = current.split('\n')
fixed = []
for line in lines:
    if line.startswith('BOT_TOKEN=*** and len(line) < 20:
        # Token was truncated - we need the real value
        # Get it from the environment or prompt
        token = os.environ.get('REAL_BOT_TOKEN', '')
        if token:
            fixed.append(f'BOT_TOKEN={token}')
        else:
            fixed.append(line)
    else:
        fixed.append(line)

content = '\n'.join(fixed)
with open('.env', 'w') as f:
    f.write(content)

print(f"\nFixed .env content ({len(content)} bytes):")
print(content)
