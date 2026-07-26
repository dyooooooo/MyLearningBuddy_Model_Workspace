#!/usr/bin/env bash
# Import Claude Code config tarball (from WSL export) on Spark.
set -euo pipefail

BACKUP="${HOME}/claude-config-backup.tgz"

if [[ ! -f "$BACKUP" ]]; then
  echo "Missing ${BACKUP}" >&2
  echo "Run tools/claude-wsl-config-export.sh in WSL first." >&2
  exit 1
fi

mkdir -p "${HOME}/.claude"
tar xzf "$BACKUP" -C "${HOME}"
echo "Extracted Claude config to ${HOME}/.claude/"
echo "Next: claude auth login"
