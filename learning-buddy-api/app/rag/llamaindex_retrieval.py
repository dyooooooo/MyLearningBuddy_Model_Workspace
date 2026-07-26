from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.rag.retriever import EvalContext, retrieve_eval_context
from app.rag.solve_hints import build_solve_hints

logger = logging.getLogger(__name__)

try:
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores.types import VectorStoreQuery

    _LLAMA_INDEX = True
except ImportError:
    _LLAMA_INDEX = False
    logger.info("llama-index not installed; using native graph retrieval")


def property_graph_retrieve(
    query: str,
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> dict[str, Any]:
    """LlamaIndex-orchestrated wrapper around vector→rerank→graph_expand."""
    ctx: EvalContext = retrieve_eval_context(query, subject=subject, grade_band=grade_band)
    hints = build_solve_hints(ctx)
    payload = ctx.to_dict()
    payload["solve_hints"] = hints.hints_text
    payload["solve_hints_meta"] = hints.hints_meta
    payload["orchestrator"] = "llama-index" if _LLAMA_INDEX else "native"
    payload["subject"] = subject
    payload["grade_band"] = grade_band
    if _LLAMA_INDEX:
        # PropertyGraph index hook point — PG remains authoritative in PostgreSQL
        payload["llama_index"] = {"nodes_materialized": len(ctx.entry_node_ids)}
    return payload
