#!/usr/bin/env bash
# Run this script in WSL to export Claude Code config for Spark import.
set -euo pipefail

BACKUP="${HOME}/claude-config-backup.tgz"
SPARK_USER="${SPARK_USER:-mylearningbuddy}"
SPARK_HOST="${SPARK_HOST:-192.168.1.10}"

items=()
for path in \
  "${HOME}/.claude/settings.json" \
  "${HOME}/.claude/settings.local.json" \
  "${HOME}/.claude/CLAUDE.md" \
  "${HOME}/.claude/hooks" \
  "${HOME}/.claude/commands" \
  "${HOME}/.claude/agents" \
  "${HOME}/.claude/memory" \
  "${HOME}/.claude/projects"
do
  if [[ -e "$path" ]]; then
    items+=("$path")
  fi
done

if [[ ${#items[@]} -eq 0 ]]; then
  echo "No Claude config found under ~/.claude/" >&2
  exit 1
fi

tar czf "$BACKUP" "${items[@]}"
echo "Created ${BACKUP}"

scp "$BACKUP" "${SPARK_USER}@${SPARK_HOST}:~/"
echo "Uploaded to ${SPARK_USER}@${SPARK_HOST}:~/claude-config-backup.tgz"
echo "On Spark run: tools/claude-spark-config-import.sh"
