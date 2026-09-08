"""Load Agent-owned figure insertion code from workspace tools/.

插图尺寸、同行、裁切清晰度只有一份实现，在 textbook-agent 的
`workspace/tools/fig_size.py` 与 `workspace/tools/figure_recrop.py`，
运行时按 `/data/agent/tools/` 只读挂载进本容器。归属见
doc/fig-code-ownership.md。
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

_REQUIRE = (
    "iter_figure_jpegs",
    "page_jpeg_settings",
    "FIGURE_CROP_DPI",
    "is_diagram_latex",
    "diagram_asset_name",
    "block_bbox",
    "block_page_no",
)
_lock = threading.Lock()
_module: ModuleType | None = None
_tried = False


def agent_home() -> Path:
    return Path(settings.agent_textbooks_dir).resolve().parent


def _seed_tools() -> Path:
    # learning-buddy-api/app/kb/this.py → repo root / agents/.../workspace/tools
    return Path(__file__).resolve().parents[3] / "agents" / "textbook-agent" / "workspace" / "tools"


def tools_dir() -> Path | None:
    mounted = agent_home() / "tools"
    if (mounted / "figure_recrop.py").is_file():
        return mounted
    seed = _seed_tools()
    if (seed / "figure_recrop.py").is_file():
        return seed
    return None


def figure_recrop() -> ModuleType | None:
    global _module, _tried
    with _lock:
        if _module is not None or _tried:
            return _module
        _tried = True
        folder = tools_dir()
        if folder is None:
            logger.info("kb figure recrop: workspace tools absent")
            return None
        path = folder / "figure_recrop.py"
        spec = importlib.util.spec_from_file_location("kb_workspace_figure_recrop", path)
        if spec is None or spec.loader is None:
            logger.warning("kb figure recrop: cannot load %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(spec.name, None)
            logger.exception("kb figure recrop: exec failed %s", path)
            return None
        missing = [name for name in _REQUIRE if not hasattr(module, name)]
        if missing:
            sys.modules.pop(spec.name, None)
            logger.warning("kb figure recrop: %s missing %s", path, ",".join(missing))
            return None
        _module = module
        logger.info("kb figure recrop: loaded %s", path)
        return _module


def page_jpeg_settings() -> tuple[int, int] | None:
    mod = figure_recrop()
    if mod is None:
        return None
    try:
        dpi, quality = mod.page_jpeg_settings()
        return int(dpi), int(quality)
    except Exception:
        return None


def reset_for_tests() -> None:
    global _module, _tried
    with _lock:
        _module = None
        _tried = False
        sys.modules.pop("kb_workspace_figure_recrop", None)
        sys.modules.pop("kb_workspace_equation_display", None)
