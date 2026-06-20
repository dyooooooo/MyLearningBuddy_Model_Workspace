#!/usr/bin/env bash
# Backup PostgreSQL (buddy) + Milvus/Qdrant + MinIO companion frames metadata.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-$HOME/opt/backups/buddy-pilot}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_ROOT}/${STAMP}"
mkdir -p "${DEST}"

POSTGRES_USER="${POSTGRES_USER:-buddy}"
POSTGRES_DB="${POSTGRES_DB:-buddy}"
PG_CONTAINER="${PG_CONTAINER:-lb-postgres}"
MILVUS_CONTAINER="${MILVUS_CONTAINER:-lb-milvus}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-lb-qdrant}"
VECTOR_BACKEND="${VECTOR_BACKEND:-milvus}"

echo "== PostgreSQL dump =="
docker exec "${PG_CONTAINER}" pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
  > "${DEST}/buddy.sql" 2>/dev/null \
  || echo "WARN: pg_dump failed (is ${PG_CONTAINER} running?)"

if [ "${VECTOR_BACKEND}" = "milvus" ]; then
  echo "== Milvus data dir archive =="
  docker exec "${MILVUS_CONTAINER}" tar -czf - /var/lib/milvus 2>/dev/null \
    > "${DEST}/milvus-data.tgz" \
    || echo "WARN: Milvus archive failed (is ${MILVUS_CONTAINER} running?)"
else
  echo "== Qdrant snapshot =="
  curl -sf -X POST "http://127.0.0.1:6333/collections/lb_corpus/snapshots" \
    -o "${DEST}/qdrant-snapshot.json" 2>/dev/null \
    || echo "WARN: Qdrant snapshot API failed"
fi

echo "== MinIO bucket list (companion-frames + milvus) =="
docker run --rm --network learning-buddy-c2 \
  minio/mc:latest alias set local http://minio:9000 minioadmin minioadmin 2>/dev/null \
  && docker run --rm --network learning-buddy-c2 minio/mc:latest ls local/ \
  > "${DEST}/minio-list.txt" 2>/dev/null \
  || echo "WARN: MinIO mc list skipped"

echo "Backup written to ${DEST}"
ls -la "${DEST}"
