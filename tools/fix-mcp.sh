#!/usr/bin/env bash
# Fix MCP: reinstall local servers + verify
set -euo pipefail

npm config set registry https://registry.npmmirror.com

npm install --prefix "$HOME/.local/lib/mcp" \
  @upstash/context7-mcp \
  @modelcontextprotocol/server-sequential-thinking \
  @modelcontextprotocol/server-memory

echo "Testing context7..."
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | timeout 5 /usr/bin/node "$HOME/.local/lib/mcp/node_modules/@upstash/context7-mcp/dist/index.js" \
  | head -1

echo ""
echo "MCP packages OK. Restart Cursor and refresh MCP in Settings."
