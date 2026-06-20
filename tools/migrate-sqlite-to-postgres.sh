#!/usr/bin/env bash
# Optional one-shot migration from legacy SQLite buddy.db to PostgreSQL.
set -euo pipefail

SQLITE_PATH="${1:-}"

if [ -z "${SQLITE_PATH}" ] || [ ! -f "${SQLITE_PATH}" ]; then
  echo "Usage: $0 /path/to/buddy.db"
  echo "No SQLite file — skip on fresh pilot install."
  exit 0
fi

echo "Legacy SQLite found: ${SQLITE_PATH}"
echo "Pilot stack uses PostgreSQL; re-ingest homework or export/import JSON manually."
echo "Corpus vectors: reindex via buddy-api startup or POST /api/v1/admin/reindex if added."
