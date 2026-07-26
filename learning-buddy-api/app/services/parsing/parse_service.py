"""ParseService — the single entry point to the document-parsing bridge.

Design:
- Callers pass an explicit ``engine`` name (no hidden auto-detection).
- There is no silent fallback: if the selected engine is not ready, a
  ParserError is raised immediately so the corpus import job fails with a
  clear error message rather than silently switching engines.
- A process-wide singleton (``get_parse_service``) avoids re-importing
  adapter modules on every call.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .registry import VALID_ENGINE_IDS
from .types import ParsedDocument, ParserError

if TYPE_CHECKING:
    from .base import Parser

logger = logging.getLogger(__name__)


def _load_adapter(
    engine: str,
    *,
    page_limit: int | None = None,
    profile_id: str = "hujiao_math_hs_v1",
) -> "Parser":
    if engine not in VALID_ENGINE_IDS:
        raise ParserError(
            f"未知解析引擎: {engine!r}。"
            f"有效值: {', '.join(sorted(VALID_ENGINE_IDS))}"
        )
    # MinerU is the only engine on this line (35B is fully isolated elsewhere).
    from .adapters.mineru_cloud import MinerUCloudAdapter
    return MinerUCloudAdapter()


class ParseService:
    """Route parse calls to the correct engine adapter.

    Engine selection is always explicit — callers supply the ``engine``
    argument. Only ``mineru_cloud`` is supported on this line.
    """

    def parse(
        self,
        pdf_bytes: bytes,
        *,
        filename: str = "import.pdf",
        engine: str = "mineru_cloud",
        progress_cb: Callable[[dict], None] | None = None,
        page_done_cb: Callable[[int, str, int], None] | None = None,
        asset_prefix: str = "",
        page_limit: int | None = None,
        page_ranges: str | None = None,
        profile_id: str = "hujiao_math_hs_v1",
    ) -> ParsedDocument:
        adapter = _load_adapter(
            engine,
            page_limit=page_limit,
            profile_id=profile_id,
        )
        if not adapter.is_ready():
            raise ParserError(
                f"解析引擎 {engine!r} 未就绪。"
                "请检查相关配置（vllm_base_url 或 Studio MinerU 设置）。"
            )
        logger.info(
            "parse_service: engine=%s filename=%s page_ranges=%s",
            engine,
            filename,
            page_ranges,
        )
        return adapter.parse(
            pdf_bytes,
            filename,
            progress_cb,
            page_done_cb=page_done_cb,
            asset_prefix=asset_prefix,
            page_limit=page_limit,
            page_ranges=page_ranges,
        )


_service: ParseService | None = None


def get_parse_service() -> ParseService:
    """Return the process-wide ParseService singleton."""
    global _service
    if _service is None:
        _service = ParseService()
    return _service


__all__ = ["ParseService", "get_parse_service"]
