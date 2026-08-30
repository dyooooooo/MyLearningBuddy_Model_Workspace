"""Load the Agent-owned TOC builder from the workspace tools/ tree."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.kb.workspace_store import read_toc

logger = logging.getLogger(__name__)


def agent_home() -> Path:
    return Path(settings.agent_textbooks_dir).resolve().parent


def build_toc_tool_path() -> Path:
    return agent_home() / "tools" / "build_toc.py"


def run_workspace_toc(book_id: str) -> dict[str, Any] | None:
    path = build_toc_tool_path()
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("kb_workspace_build_toc", path)
    if spec is None or spec.loader is None:
        logger.warning("kb workspace toc: cannot load %s", path)
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        builder = getattr(module, "build_toc", None)
        if builder is None:
            logger.warning("kb workspace toc: build_toc() missing in %s", path)
            return None
        result = builder(str(agent_home()), book_id)
    except Exception:
        logger.exception("kb workspace toc failed book=%s", book_id)
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        logger.warning("kb workspace toc declined book=%s result=%s", book_id, result)
        return None
    built_id = str(result.get("book_id") or "").strip()
    if built_id and built_id != book_id:
        logger.warning("kb workspace toc wrote other book=%s wanted=%s", built_id, book_id)
        return None
    toc = read_toc(book_id)
    if not toc.get("entries"):
        return None
    return toc
