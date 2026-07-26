#!/usr/bin/env bash
# Push DeepTutor from Windows (WSL) to Spark.
# Run this script ON WSL (not on Spark).
set -euo pipefail

SPARK_HOST="${SPARK_HOST:-mylearningbuddy@192.168.1.10}"
DEST="${DEST:-~/MyLearningBuddy_Model_Workspace/DeepTutor}"

SRC_CANDIDATES=(
  "/mnt/c/Users/Eason/Downloads/DeepTutor-1.4.11/DeepTutor-1.4.11"
  "/mnt/c/Users/Eason/Downloads/DeepTutor-1.4.12/DeepTutor-1.4.12"
  "/mnt/d/01_coding/deeptutor"
  "/mnt/d/01_coding/DeepTutor"
)

src=""
for candidate in "${SRC_CANDIDATES[@]}"; do
  if [[ -d "$candidate" && -f "$candidate/pyproject.toml" ]]; then
    src="$candidate"
    break
  fi
done

if [[ -z "$src" ]]; then
  echo "ERROR: DeepTutor not found. Tried:" >&2
  printf '  - %s\n' "${SRC_CANDIDATES[@]}" >&2
  exit 1
fi

echo "Source: $src"
echo "Target: $SPARK_HOST:$DEST"

ssh "$SPARK_HOST" "mkdir -p $DEST"

rsync -avz --progress --delete \
  --exclude 'node_modules' \
  --exclude '.next' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude 'venv' \
  --exclude '.pytest_cache' \
  --exclude 'data/knowledge_bases' \
  "$src/" \
  "$SPARK_HOST:$DEST/"

echo ""
echo "Done. On Spark verify:"
echo "  ssh $SPARK_HOST 'grep ^version $DEST/pyproject.toml | head -1'"
echo "  ssh $SPARK_HOST 'ls $DEST/deeptutor 2>/dev/null | head'"
