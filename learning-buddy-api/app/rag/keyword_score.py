from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z]+|\d+(?:\.\d+)?|[^\s\w]", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.strip()]


@lru_cache(maxsize=8)
def _kp_keyword_index(subject: str, grade_band: str) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    corpus = Path(settings.corpus_dir)
    syllabus_path = corpus / "syllabus" / f"{subject}_{grade_band}.json"
    bank_path = corpus / "question-bank" / f"{subject}_{grade_band}.jsonl"

    if syllabus_path.is_file():
        data = json.loads(syllabus_path.read_text(encoding="utf-8"))
        nodes = data.get("nodes", data if isinstance(data, list) else [])
        for n in nodes:
            kp_id = n.get("id", "")
            if not kp_id:
                continue
            parts = [n.get("label", ""), n.get("description", "")]
            parts.extend(n.get("keywords", []))
            tokens = set()
            for p in parts:
                tokens.update(tokenize(str(p)))
            index[kp_id] = tokens

    if bank_path.is_file():
        for line in bank_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            kp_id = row.get("kp_id", "")
            if not kp_id:
                continue
            tokens = index.setdefault(kp_id, set())
            tokens.update(tokenize(row.get("stem", "")))

    return index


def keyword_score_for_kp(
    query: str,
    kp_id: str,
    doc_text: str = "",
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> float:
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0

    kp_tokens = _kp_keyword_index(subject, grade_band).get(kp_id, set())
    if doc_text:
        kp_tokens = kp_tokens | set(tokenize(doc_text))

    if not kp_tokens:
        return 0.0

    overlap = len(q_tokens & kp_tokens)
    return min(1.0, overlap / max(1, min(len(q_tokens), 3)))


def hybrid_score(vector_score: float, keyword_score: float, alpha: float | None = None) -> float:
    """Legacy linear blend — prefer RRF in retriever.search when enabled."""
    a = settings.rag_hybrid_alpha if alpha is None else alpha
    return max(0.0, min(1.0, a * vector_score + (1.0 - a) * keyword_score))


def lexical_overlap_score(query: str, doc_text: str) -> float:
    """Sparse/lexical channel score (token overlap) for RRF hybrid."""
    q_tokens = set(tokenize(query))
    if not q_tokens or not doc_text:
        return 0.0
    d_tokens = set(tokenize(doc_text))
    if not d_tokens:
        return 0.0
    overlap = len(q_tokens & d_tokens)
    return min(1.0, overlap / max(1, min(len(q_tokens), 8)))
