"""判例向量索引：独立 Milvus collection（35B 自有）。

隔离契约：
- 复用共享 RAG 栈的**基础设施**——``app.rag.embedder.embed_texts``（同 mathjax
  桥一样的纯共享 util）与 pymilvus 连接（``settings.milvus_uri``）。
- 但落在**独立 collection**（``settings.import35b_caselaw_collection``），有自己
  的最小 schema——绝不写语料库 collection ``lb_corpus``，绝不使用
  ``CorpusDocument`` / kp_id / published 那套语料语义字段。
- 任何基础设施缺失（无 pymilvus / Milvus 不可达 / 无 embedder）→ 全部方法降级为
  no-op 或空结果，绝不抛异常阻断导入或裁决主流程。

canonical 事实来源是 Postgres ``import35b_caselaw`` 表；本模块只做向量检索加速。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_DENSE = "dense"
_TEXT_MAX = 4096


@dataclass
class CaseHit:
    case_id: str
    kind: str
    suspect_type: str
    fingerprint: str
    score: float
    text: str


@lru_cache(maxsize=1)
def _pymilvus():
    """Lazy import so the module loads even where pymilvus is absent."""
    try:
        import pymilvus  # noqa: F401
        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )

        return {
            "Collection": Collection,
            "CollectionSchema": CollectionSchema,
            "DataType": DataType,
            "FieldSchema": FieldSchema,
            "connections": connections,
            "utility": utility,
        }
    except Exception as exc:  # noqa: BLE001
        logger.info("caselaw: pymilvus unavailable (%s); vector index disabled", exc)
        return None


@lru_cache(maxsize=1)
def _connected() -> bool:
    m = _pymilvus()
    if m is None:
        return False
    try:
        m["connections"].connect(alias="default", uri=settings.milvus_uri)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("caselaw: Milvus connect failed (%s); vector index disabled", exc)
        return False


def _embed(texts: list[str]) -> list[list[float]] | None:
    """Reuse the shared embedder; return None on any failure (degrade to no-op)."""
    if not texts:
        return []
    try:
        from app.rag.embedder import embed_texts

        vecs = embed_texts(texts)
        if vecs and len(vecs) == len(texts):
            return vecs
    except Exception as exc:  # noqa: BLE001
        logger.info("caselaw: embed unavailable (%s)", exc)
    return None


def available() -> bool:
    """True only when the whole vector path is usable (pymilvus + connection)."""
    return _pymilvus() is not None and _connected()


def _collection_name() -> str:
    return settings.import35b_caselaw_collection


def _ensure_collection(dim: int):
    m = _pymilvus()
    if m is None or not _connected():
        return None
    utility = m["utility"]
    name = _collection_name()
    if utility.has_collection(name):
        return m["Collection"](name)
    FieldSchema = m["FieldSchema"]
    DataType = m["DataType"]
    fields = [
        FieldSchema(name="pk", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="case_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="kind", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="suspect_type", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="subject", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="grade_band", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="fingerprint", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name=_DENSE, dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    schema = m["CollectionSchema"](fields, description="35B caselaw (isolated)")
    col = m["Collection"](name, schema)
    col.create_index(
        field_name=_DENSE,
        index_params={
            "index_type": "HNSW",
            "metric_type": "COSINE",
            "params": {"M": 16, "efConstruction": 200},
        },
    )
    return col


def index_case(
    *,
    case_id: str,
    kind: str,
    suspect_type: str,
    subject: str,
    grade_band: str,
    fingerprint: str,
    text: str,
) -> bool:
    """Upsert one case vector. Returns True on success, False (no-op) otherwise."""
    if not available():
        return False
    vecs = _embed([text[:_TEXT_MAX]])
    if not vecs:
        return False
    try:
        col = _ensure_collection(len(vecs[0]))
        if col is None:
            return False
        col.load()
        # idempotent: drop any prior row for this case_id before insert
        if col.num_entities > 0:
            safe = case_id.replace('"', "")
            col.delete(f'case_id in ["{safe}"]')
        col.insert(
            [
                [case_id],
                [kind[:16]],
                [suspect_type[:64]],
                [subject[:32]],
                [grade_band[:16]],
                [fingerprint[:32]],
                [text[:8000]],
                vecs,
            ]
        )
        col.flush()
        col.load()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("caselaw: index_case failed (%s)", exc)
        return False


def search_cases(
    text: str,
    *,
    kind: str,
    subject: str,
    grade_band: str,
    top_k: int | None = None,
) -> list[CaseHit]:
    """Fuzzy retrieve similar prior cases of the given kind. Empty on any failure."""
    if not available() or not text.strip():
        return []
    m = _pymilvus()
    utility = m["utility"]
    name = _collection_name()
    if not utility.has_collection(name):
        return []
    vecs = _embed([text[:_TEXT_MAX]])
    if not vecs:
        return []
    k = top_k or settings.import35b_caselaw_top_k
    try:
        col = m["Collection"](name)
        col.load()
        expr = (
            f'kind == "{kind}" && subject == "{subject}" '
            f'&& grade_band == "{grade_band}"'
        )
        results = col.search(
            data=vecs,
            anns_field=_DENSE,
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=k,
            expr=expr,
            output_fields=["case_id", "kind", "suspect_type", "fingerprint", "text"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("caselaw: search failed (%s)", exc)
        return []
    def _field(entity: Any, name: str) -> str:
        val = entity.get(name) if hasattr(entity, "get") else getattr(entity, name, "")
        return str(val or "")

    hits: list[CaseHit] = []
    for group in results:
        for p in group:
            ent = p.entity
            hits.append(
                CaseHit(
                    case_id=_field(ent, "case_id"),
                    kind=_field(ent, "kind"),
                    suspect_type=_field(ent, "suspect_type"),
                    fingerprint=_field(ent, "fingerprint"),
                    score=float(p.score),
                    text=_field(ent, "text"),
                )
            )
    return hits


def delete_case_vectors(case_ids: list[str]) -> int:
    """Drop Milvus rows for the given canonical case ids. No-op when vector path unavailable."""
    if not case_ids or not available():
        return 0
    m = _pymilvus()
    name = _collection_name()
    if m is None or not m["utility"].has_collection(name):
        return 0
    removed = 0
    try:
        col = m["Collection"](name)
        col.load()
        for case_id in case_ids:
            safe = case_id.replace('"', "")
            col.delete(f'case_id in ["{safe}"]')
            removed += 1
        col.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("caselaw: delete_case_vectors failed (%s)", exc)
        return 0
    return removed


def count() -> int:
    if not available():
        return 0
    m = _pymilvus()
    name = _collection_name()
    if not m["utility"].has_collection(name):
        return 0
    try:
        col = m["Collection"](name)
        col.flush()
        return int(col.num_entities)
    except Exception:  # noqa: BLE001
        return 0
