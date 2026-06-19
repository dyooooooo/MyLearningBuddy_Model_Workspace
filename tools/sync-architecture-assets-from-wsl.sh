#!/usr/bin/env bash
# Pull Learning Buddy master architecture assets from WSL to Spark.
# Run this script ON WSL (not on Spark).
set -euo pipefail

SPARK_HOST="${SPARK_HOST:-mylearningbuddy@192.168.3.200}"
WORKSPACE="${WORKSPACE:-MyLearningBuddy_Model_Workspace}"
DEST="${DEST:-~/${WORKSPACE}/doc/assets}"
SRC_CANDIDATES=(
  "/mnt/d/01_coding/LearningStack/MyLearningBuddy/docs/architecture/assets"
  "$HOME/exam-ocr/docs/assets"
  "/mnt/d/01_coding/LearningStack/exam-ocr/docs/assets"
  "/mnt/d/01_coding/LearningStack/docs/assets"
)

src=""
for candidate in "${SRC_CANDIDATES[@]}"; do
  if [[ -f "$candidate/learning-buddy-master-architecture.svg" ]]; then
    src="$candidate"
    break
  fi
done

if [[ -z "$src" ]]; then
  echo "ERROR: learning-buddy-master-architecture.svg not found in:" >&2
  printf '  - %s\n' "${SRC_CANDIDATES[@]}" >&2
  exit 1
fi

echo "Source: $src"
echo "Target: $SPARK_HOST:$DEST"

ssh "$SPARK_HOST" "mkdir -p $DEST"

rsync -avz \
  "$src/learning-buddy-master-architecture.svg" \
  "$SPARK_HOST:$DEST/"

echo "Done. Spark SVG: $DEST/learning-buddy-master-architecture.svg"
echo "On Spark, regenerate PNG (from workspace root):"
echo "  tools/replace-architecture-from-svg.sh"
