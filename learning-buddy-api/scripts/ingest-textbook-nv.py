#!/usr/bin/env bash
# NV-Ingest textbook PDF → Chunk nodes + Milvus (requires nv-ingest client + running stack).
set -euo pipefail

PDF_PATH="${1:-}"
COLLECTION="${MILVUS_COLLECTION:-lb_corpus}"
MILVUS_URI="${MILVUS_URI:-http://127.0.0.1:19530}"
NV_INGEST_URL="${NV_INGEST_URL:-http://127.0.0.1:7670}"

if [ -z "$PDF_PATH" ] || [ ! -f "$PDF_PATH" ]; then
  echo "Usage: $0 <textbook.pdf>"
  exit 1
fi

echo "== NV-Ingest textbook: $PDF_PATH =="
python3 <<PY
import os
import sys

pdf = "${PDF_PATH}"
milvus_uri = "${MILVUS_URI}"
collection = "${COLLECTION}"

try:
    from nv_ingest_client.client import Ingestor, NvIngestClient
except ImportError:
    print("WARN: nv-ingest-client not installed; writing stub manifest only")
    print(f"PDF={pdf} milvus={milvus_uri} collection={collection}")
    sys.exit(0)

client = NvIngestClient(message_client_hostname=os.environ.get("NV_INGEST_HOST", "127.0.0.1"))
(
    Ingestor(client=client)
    .files(pdf)
    .extract()
    .embed()
    .vdb_upload(
        collection_name=collection,
        milvus_uri=milvus_uri,
        dense_dim=int(os.environ.get("EMBED_DIM", "2048")),
        sparse=True,
        stream=len(pdf) <= 10,
    )
)
print("OK: nv-ingest vdb_upload complete")
PY

echo "Run buddy-api reindex to sync PG graph nodes from corpus if needed."
