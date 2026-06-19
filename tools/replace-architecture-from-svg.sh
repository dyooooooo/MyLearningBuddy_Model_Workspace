#!/usr/bin/env bash
# Replace doc/assets PNG from authoritative SVG (default: <workspace>/doc/assets).
set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS_DIR="${1:-$WORKSPACE_ROOT/doc/assets}"
SVG="$ASSETS_DIR/learning-buddy-master-architecture.svg"
PNG="$ASSETS_DIR/learning-buddy-master-architecture.png"
VENV="$WORKSPACE_ROOT/.venv-assets"

if [[ ! -f "$SVG" ]]; then
  echo "ERROR: SVG not found: $SVG" >&2
  exit 1
fi

if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q cairosvg pillow
fi

"$VENV/bin/python3" << PY
import cairosvg
from pathlib import Path

svg = Path("$SVG")
png = Path("$PNG")
cairosvg.svg2png(url=str(svg), write_to=str(png), scale=2.0)
print(f"Wrote {png} ({png.stat().st_size} bytes)")
PY

file "$SVG" "$PNG"
