"""Load Agent-owned page-source sanitizer from workspace tools/.

官方源 ``pages/*.md`` 不得带 ``::: kb-*`` 围栏或残片。唯一实现在
textbook-agent 的 ``workspace/tools/source_text.py``，运行时按
``/data/agent/tools/source_text.py`` 只读挂载。卷缺失时回退内置正则，
buddy-api 不会因为 agent 没起来而崩。
"""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
import threading
from pathlib import Path
from types import ModuleType

from app.config import settings

logger = logging.getLogger(__name__)

_REQUIRE = ("sanitize_source", "sanitize_mid")
_lock = threading.Lock()
_module: ModuleType | None = None
_tried = False

_FENCE = re.compile(r"::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::")
_OPENER = re.compile(r"::: kb-[\w-]+")
_NAME_LINE = re.compile(
    r"^[ \t]*(?::: )?(kb-(?:center|title|subtitle|author|section(?:-center)?|"
    r"verse(?:-center)?|date|list-item|lead|solution(?:-tail)?|page-toc|"
    r"table-wrap|section-icon|drama|stage))[ \t]*$"
)
_CLOSER = re.compile(r"^[ \t]*:::[ \t]*$")


def agent_home() -> Path:
    return Path(settings.agent_textbooks_dir).resolve().parent


def _seed_tools() -> Path:
    return Path(__file__).resolve().parents[3] / "agents" / "textbook-agent" / "workspace" / "tools"


def tools_dir() -> Path | None:
    mounted = agent_home() / "tools"
    if (mounted / "source_text.py").is_file():
        return mounted
    seed = _seed_tools()
    if (seed / "source_text.py").is_file():
        return seed
    return None


def source_text() -> ModuleType | None:
    global _module, _tried
    with _lock:
        if _module is not None or _tried:
            return _module
        _tried = True
        folder = tools_dir()
        if folder is None:
            logger.info("kb source_text: workspace tools absent")
            return None
        path = folder / "source_text.py"
        spec = importlib.util.spec_from_file_location("kb_workspace_source_text", path)
        if spec is None or spec.loader is None:
            logger.warning("kb source_text: cannot load %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(spec.name, None)
            logger.exception("kb source_text: exec failed %s", path)
            return None
        missing = [name for name in _REQUIRE if not hasattr(module, name)]
        if missing:
            sys.modules.pop(spec.name, None)
            logger.warning("kb source_text: %s missing %s", path, ",".join(missing))
            return None
        _module = module
        logger.info("kb source_text: loaded %s", path)
        return _module


def _fallback_source(text: str) -> str:
    out = text or ""
    prev = None
    while prev != out:
        prev = out
        out = _FENCE.sub(lambda m: (m.group(2) or "").strip(), out)
    lines: list[str] = []
    for line in out.splitlines():
        if _CLOSER.match(line) or _NAME_LINE.match(line):
            continue
        cleaned = re.sub(
            r"(?:^|(?<=\s))(kb-(?:center|title|subtitle|author|section(?:-center)?|"
            r"verse(?:-center)?|date|list-item|lead|solution(?:-tail)?|page-toc|"
            r"table-wrap|section-icon|drama|stage))(?=\s|[^\w-]|$)",
            "",
            _OPENER.sub("", line),
        )
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
        if not cleaned.strip() and (_OPENER.search(line) or "kb-" in line):
            continue
        lines.append(cleaned)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return body.strip() + ("\n" if body.strip() else "")


def sanitize_source(text: str) -> str:
    mod = source_text()
    if mod is not None:
        return str(mod.sanitize_source(text))
    return _fallback_source(text)


def sanitize_mid(text: str) -> str:
    mod = source_text()
    if mod is not None:
        return str(mod.sanitize_mid(text))
    return text or ""


def reset_for_tests() -> None:
    global _module, _tried
    with _lock:
        _module = None
        _tried = False
        sys.modules.pop("kb_workspace_source_text", None)
