#!/bin/bash
set -eu
payload=$(cat || true)
log="${HOME:-/data/agent}/logs/hooks.log"
mkdir -p "$(dirname "$log")"
printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$payload" >> "$log"
printf '%s\n' '{"permission":"allow"}'
