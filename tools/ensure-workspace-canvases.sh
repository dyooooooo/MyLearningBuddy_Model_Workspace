#!/usr/bin/env bash
# Point $WORKSPACE/canvases at the Cursor project canvases dir this window is using.
set -euo pipefail

WORKSPACE="${1:-$HOME/MyLearningBuddy_Model_Workspace}"
PROJ_ROOT="${HOME}/.cursor/projects"
best=""
best_t=0

for terminals in "$PROJ_ROOT"/*/terminals; do
  if [[ ! -d "$terminals" ]]; then
    continue
  fi
  mt="$(stat -c %Y "$terminals" 2>/dev/null || echo 0)"
  if [[ "$mt" -gt "$best_t" ]]; then
    best_t="$mt"
    best="$(dirname "$terminals")"
  fi
done

if [[ -z "$best" ]]; then
  echo "no Cursor project with a terminals/ directory" >&2
  exit 1
fi

managed="${best}/canvases"
mkdir -p "$managed"
ln -sfn "$managed" "${WORKSPACE}/canvases"
echo "$managed"