"""Load the Agent-owned TOC lexicon from the workspace tools/ tree.

目录词法只有一份实现，在 textbook-agent 的 `workspace/tools/toc_lexicon.py`，
运行时按 `/data/agent/tools/toc_lexicon.py` 只读挂载进本容器；词法表本身是
`/data/agent/configs/toc_lexicon.yaml`（同样只读挂载）。

导入期用它判「这一整页是不是印刷目录」（`kb/mid.py::_tag_toc`）与「这一行是不是
目录条目」（`kb/toc.py`）。agent 卷缺失时返回 None，调用方回退内置正则，
所以 buddy-api 不会因为 agent 没起来而崩。归属清单见 doc/toc-code-ownership.md。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType

from app.config import settings

logger = logging.getLogger(__name__)

_REQUIRE = ("strip_page", "match_line", "is_column_label", "has_skip_prefix")
_lock = threading.Lock()
_module: ModuleType | None = None
_tried = False


def agent_home() -> Path:
    return Path(settings.agent_textbooks_dir).resolve().parent


def lexicon_tool_path() -> Path:
    return agent_home() / "tools" / "toc_lexicon.py"


def lexicon() -> ModuleType | None:
    """Shared lexicon module, or None when the agent volume is not mounted."""
    global _module, _tried
    with _lock:
        if _module is not None or _tried:
            return _module
        _tried = True
        path = lexicon_tool_path()
        if not path.is_file():
            logger.info("kb toc lexicon: %s absent, using built-in regexes", path)
            return None
        spec = importlib.util.spec_from_file_location("kb_workspace_toc_lexicon", path)
        if spec is None or spec.loader is None:
            logger.warning("kb toc lexicon: cannot load %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        # 必须先进 sys.modules 再 exec：模块里的 @dataclass 会去
        # sys.modules[cls.__module__] 查注解，缺了就 AttributeError。
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(spec.name, None)
            logger.exception("kb toc lexicon: exec failed %s", path)
            return None
        missing = [name for name in _REQUIRE if not hasattr(module, name)]
        if missing:
            sys.modules.pop(spec.name, None)
            logger.warning("kb toc lexicon: %s missing %s", path, ",".join(missing))
            return None
        if not module.lexicon_path().is_file():
            sys.modules.pop(spec.name, None)
            logger.warning("kb toc lexicon: yaml missing at %s", module.lexicon_path())
            return None
        _module = module
        logger.info("kb toc lexicon: loaded %s (yaml %s)", path, module.lexicon_path())
        return _module


def reset_for_tests() -> None:
    global _module, _tried
    with _lock:
        _module = None
        _tried = False
