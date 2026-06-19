#!/usr/bin/env bash
# Install Node.js 22 to ~/.local (no sudo)
set -euo pipefail

NODE_VERSION="22.16.0"
INSTALL_DIR="$HOME/.local/node"
ARCH="linux-arm64"
TARBALL="node-v${NODE_VERSION}-${ARCH}.tar.xz"
URL="https://nodejs.org/dist/v${NODE_VERSION}/${TARBALL}"

if command -v node &>/dev/null && [[ "$(node --version)" == v22* ]]; then
  echo "Node.js already installed: $(node --version)"
  exit 0
fi

echo "Downloading Node.js ${NODE_VERSION}..."
curl -fsSL "$URL" -o "/tmp/${TARBALL}"

mkdir -p "$INSTALL_DIR"
tar -xJf "/tmp/${TARBALL}" -C "$INSTALL_DIR" --strip-components=1
rm "/tmp/${TARBALL}"

MARKER='# DGX Spark — user Node.js'
if ! grep -q "$MARKER" "$HOME/.bashrc" 2>/dev/null; then
  cat >> "$HOME/.bashrc" <<EOF

$MARKER
export PATH="\$HOME/.local/node/bin:\$PATH"
EOF
fi

export PATH="$INSTALL_DIR/bin:$PATH"
echo "Installed: $(node --version) · npm $(npm --version)"
echo "Run: source ~/.bashrc"
