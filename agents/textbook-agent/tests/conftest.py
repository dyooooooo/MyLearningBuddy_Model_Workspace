from __future__ import annotations

import os
import tempfile

# 每次 pytest 用独立 AGENT_HOME。固定路径（原来的 /tmp/textbook-agent-test）会被
# `ensure_workspace` 的「文件已存在就不覆盖」逻辑锁住：上一轮播下的 tools/*.py 会一直
#赢过镜像里的新版本，于是改了 workspace/tools/ 却跑的还是旧代码。
os.environ.setdefault("AGENT_HOME", tempfile.mkdtemp(prefix="textbook-agent-test-"))
os.environ.setdefault("TEXTBOOK_AGENT_TOKEN", "test-token")
os.environ.setdefault("PAGE_SYNC_WATCH", "0")
