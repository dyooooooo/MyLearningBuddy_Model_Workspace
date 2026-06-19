#!/usr/bin/env bash
# DGX Spark AI Lab — one-time system setup (requires sudo for apt packages)
set -euo pipefail

echo "=== DGX Spark AI Lab Setup ==="

if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  echo 'source "$HOME/.local/bin/env"' >> "$HOME/.bashrc"
fi

if ! command -v node &>/dev/null; then
  echo "Installing Node.js (required for local MCP servers)..."
  sudo apt-get update -qq
  sudo apt-get install -y nodejs npm
fi

if ! groups "$USER" | grep -q docker; then
  echo "Adding $USER to docker group..."
  sudo usermod -aG docker "$USER"
  echo "Run: newgrp docker"
fi

WORKSPACE_DIR="${WORKSPACE_DIR:-MyLearningBuddy_Model_Workspace}"

mkdir -p "$HOME/$WORKSPACE_DIR"/{doc,agents,notebooks,scripts,configs,tools}

# models/ is a symlink to ~/opt/models — create only if missing
if [[ ! -e "$HOME/$WORKSPACE_DIR/models" ]]; then
  ln -s ../opt/models "$HOME/$WORKSPACE_DIR/models"
fi

if [[ ! -f "$HOME/.bashrc.d/dgx-spark-env.sh" ]] && [[ -f "$(dirname "$0")/env.example" ]]; then
  mkdir -p "$HOME/.bashrc.d"
  cp "$(dirname "$0")/env.example" "$HOME/.bashrc.d/dgx-spark-env.sh"
  grep -q 'bashrc.d/dgx-spark-env' "$HOME/.bashrc" 2>/dev/null || \
    echo '[[ -f "$HOME/.bashrc.d/dgx-spark-env.sh" ]] && source "$HOME/.bashrc.d/dgx-spark-env.sh"' >> "$HOME/.bashrc"
  echo "Created ~/.bashrc.d/dgx-spark-env.sh — edit API keys there"
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit ~/.bashrc.d/dgx-spark-env.sh with API keys"
echo "  2. source ~/.bashrc"
echo "  3. Restart Cursor to reload MCP servers"
echo "  4. Run: tools/check-dgx-health.sh"
echo "  5. Open: http://localhost:11000"
