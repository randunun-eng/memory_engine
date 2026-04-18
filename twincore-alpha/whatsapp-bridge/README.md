# whatsapp-bridge

This directory contains only the Dockerfile. It clones lharries/whatsapp-mcp at build time:
https://github.com/lharries/whatsapp-mcp

The Dockerfile builds the Go bridge binary and exposes port 8080.

First run:
- Container prints a QR code URL to stdout
- User opens URL on any device, scans with phone (WhatsApp > Settings > Linked Devices)
- After scan, bridge begins syncing messages to /app/whatsapp-bridge/store/messages.db

Volumes in docker-compose.yml persist:
- ./whatsapp-data → /app/whatsapp-bridge/store (messages SQLite)
- ./whatsapp-auth → /app/whatsapp-bridge/auth_info (auth credentials)
