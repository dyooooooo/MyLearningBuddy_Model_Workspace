#!/usr/bin/env bash
# DGX Spark — domestic mirrors and alternatives (no proxy)
# User-level config (no sudo). System config: tools/setup-china-mirrors-system.sh
set -euo pipefail

echo "=== DGX Spark China Mirrors Setup (no proxy) ==="

# --- User: pip ---
mkdir -p "$HOME/.pip"
cat > "$HOME/.pip/pip.conf" <<'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
EOF
echo "OK: ~/.pip/pip.conf"

# --- User: shell env ---
mkdir -p "$HOME/.bashrc.d"
cat > "$HOME/.bashrc.d/china-mirrors.sh" <<'EOF'
# DGX Spark — domestic mirrors (no proxy). Managed by tools/setup-china-mirrors.sh

export HF_ENDPOINT="https://hf-mirror.com"
export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
export GOPROXY="https://goproxy.cn,direct"
export RUSTUP_DIST_SERVER="https://rsproxy.cn"
export RUSTUP_UPDATE_ROOT="https://rsproxy.cn/rustup"
EOF
echo "OK: ~/.bashrc.d/china-mirrors.sh"

MARKER='bashrc.d/china-mirrors.sh'
if ! grep -qF "$MARKER" "$HOME/.bashrc" 2>/dev/null; then
  echo '[[ -f "$HOME/.bashrc.d/china-mirrors.sh" ]] && source "$HOME/.bashrc.d/china-mirrors.sh"' >> "$HOME/.bashrc"
  echo "OK: appended china-mirrors source to ~/.bashrc"
else
  echo "OK: ~/.bashrc already sources china-mirrors.sh"
fi

# --- User: npm ---
npm config set registry https://registry.npmmirror.com 2>/dev/null || true
echo 'registry=https://registry.npmmirror.com/' > "$HOME/.npmrc"
echo "OK: ~/.npmrc"

# --- User: git GitHub rewrite (both https and git protocols) ---
git config --global --unset-all url."https://gitclone.com/github.com/".insteadOf 2>/dev/null || true
git config --global --add url."https://gitclone.com/github.com/".insteadOf "https://github.com/"
git config --global --add url."https://gitclone.com/github.com/".insteadOf "git://github.com/"
echo "OK: git global url rewrite for GitHub"

echo ""
echo "User-level mirrors applied."
echo ""
echo "Next (requires sudo password):"
echo "  tools/setup-china-mirrors-system.sh"
echo ""
echo "Then:"
echo "  source ~/.bashrc"
echo "  tools/check-dgx-health.sh"
