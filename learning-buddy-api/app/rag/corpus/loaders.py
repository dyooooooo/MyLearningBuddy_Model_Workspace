from __future__ import annotations

import json
import re
from pathlib import Path

from app.rag.corpus.schema import CorpusDocument
from app.services.corpus_graph import add_edge, upsert_node
from app.db.models import CorpusTagLink
from app.services.corpus_graph import _now_iso

_CORPUS_FILE = re.compile(r"^(?P<subject>[a-z]+)_(?P<grade>[a-z0-9]+)\.(json|jsonl)$")


def parse_corpus_name(path: Path) -> tuple[str, str] | None:
    m = _CORPUS_FILE.match(path.name)
    if not m:
        return None
    return m.group("subject"), m.group("grade")


def _doc(
    *,
    doc_id: str,
    doc_type: str,
    subject: str,
    grade_band: str,
    kp_id: str,
    text: str,
    label: str = "",
    summary: str = "",
    parent_id: str = "",
    tags: list[str] | None = None,
    links: dict | None = None,
    meta: dict | None = None,
    node_type: str | None = None,
) -> CorpusDocument:
    return CorpusDocument(
        doc_id=doc_id,
        doc_type=doc_type,  # type: ignore[arg-type]
        subject=subject,
        grade_band=grade_band,
        kp_id=kp_id,
        text=text,
        label=label,
        summary=summary or text[:240],
        full_text=text,
        parent_id=parent_id,
        tags=tags or [],
        links=links or {},
        meta=meta or {},
        node_type=node_type,  # type: ignore[arg-type]
    )


def load_syllabus(path: Path, subject: str, grade_band: str) -> list[CorpusDocument]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("nodes", data if isinstance(data, list) else [])
    docs: list[CorpusDocument] = []
    for n in nodes:
        kp_id = n.get("id", "")
        if not kp_id:
            continue
        label = n.get("label", kp_id)
        desc = n.get("description", "")
        kws = " ".join(n.get("keywords", []))
        text = f"{label} {desc} {kws}".strip()
        summary = n.get("summary") or f"{label}：{desc}"[:200]
        tags = n.get("tags") or [f"grade_band:{grade_band}", f"subject:{subject}"]
        links = n.get("links") or {}
        docs.append(
            _doc(
                doc_id=kp_id,
                doc_type="syllabus",
                node_type="kp",
                subject=subject,
                grade_band=grade_band,
                kp_id=kp_id,
                text=text,
                label=label,
                summary=summary,
                parent_id=n.get("parent_id") or "",
                tags=tags,
                links=links,
                meta={"level": str(n.get("level", 1))},
            )
        )
        for kw in n.get("keywords", []):
            kw = str(kw).strip()
            if kw:
                docs.append(
                    _doc(
                        doc_id=f"kw:{kp_id}:{hash(kw) & 0xFFFFFFFF:08x}",
                        doc_type="keyword",
                        node_type="kp",
                        subject=subject,
                        grade_band=grade_band,
                        kp_id=kp_id,
                        text=f"{label} {kw}",
                        label=label,
                        summary=f"{label} 关键词 {kw}",
                        parent_id=n.get("parent_id") or "",
                        tags=tags + [f"keyword:{kw}"],
                        meta={"keyword": kw},
                    )
                )
    return docs


def load_question_bank(path: Path, subject: str, grade_band: str) -> list[CorpusDocument]:
    if not path.is_file():
        return []
    docs: list[CorpusDocument] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        stem = row.get("stem", "").strip()
        kp_id = row.get("kp_id", "")
        if not stem or not kp_id:
            continue
        stem_hash = abs(hash(stem)) & 0xFFFFFFFF
        node_id = row.get("id") or f"qb:{subject}:{grade_band}:{kp_id}:{stem_hash:08x}"
        qtype = row.get("question_type", "")
        tags = list(row.get("tags") or [
            f"grade_band:{grade_band}",
            f"subject:{subject}",
            f"kp:{kp_id}",
        ])
        if qtype:
            qtag = f"question_type:{qtype}"
            if qtag not in tags:
                tags.append(qtag)
        summary = row.get("summary") or stem[:200]
        docs.append(
            _doc(
                doc_id=node_id,
                doc_type="question_bank",
                node_type="question",
                subject=subject,
                grade_band=grade_band,
                kp_id=kp_id,
                text=stem,
                label=row.get("label", kp_id),
                summary=summary,
                tags=tags,
                links=row.get("links") or {},
                meta={
                    "source": row.get("source", "sample"),
                    "difficulty": str(row.get("difficulty", "medium")),
                    "answer": row.get("answer", ""),
                    "expected_dwell_sec": row.get("expected_dwell_sec"),
                    "line": i,
                    "question_type": qtype,
                },
            )
        )
    return docs


def load_json_nodes(path: Path, subject: str, grade_band: str, node_type: str, doc_type: str) -> list[CorpusDocument]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("nodes", data if isinstance(data, list) else [])
    docs: list[CorpusDocument] = []
    for row in items:
        nid = row.get("id", "")
        if not nid:
            continue
        text = row.get("text") or row.get("content") or row.get("formula") or row.get("steps", "")
        if isinstance(text, list):
            text = " ".join(str(x) for x in text)
        text = str(text).strip()
        kp_id = row.get("kp_id") or row.get("primary_kp") or ""
        summary = row.get("summary") or text[:240]
        tags = row.get("tags") or [f"grade_band:{grade_band}", f"node_type:{node_type}"]
        docs.append(
            _doc(
                doc_id=nid,
                doc_type=doc_type,  # type: ignore[arg-type]
                node_type=node_type,  # type: ignore[arg-type]
                subject=subject,
                grade_band=grade_band,
                kp_id=kp_id,
                text=text or summary,
                label=row.get("label", nid),
                summary=summary,
                parent_id=row.get("parent_id") or row.get("textbook_id") or "",
                tags=tags,
                links=row.get("links") or {},
                meta={k: v for k, v in row.items() if k not in ("id", "text", "summary", "tags", "links")},
            )
        )
    return docs


def load_all_corpus(corpus_dir: Path) -> list[CorpusDocument]:
    docs: list[CorpusDocument] = []
    syllabus_dir = corpus_dir / "syllabus"
    bank_dir = corpus_dir / "question-bank"
    for subdir, node_type, doc_type in [
        ("textbook", "textbook", "textbook"),
        ("sections", "section", "section"),
        ("methods", "method", "method"),
        ("shortcuts", "shortcut", "shortcut"),
        ("chunks", "chunk", "chunk"),
    ]:
        dpath = corpus_dir / subdir
        if dpath.is_dir():
            for p in sorted(dpath.glob("*.json")):
                parsed = parse_corpus_name(p)
                if parsed:
                    docs.extend(load_json_nodes(p, *parsed, node_type=node_type, doc_type=doc_type))
    if syllabus_dir.is_dir():
        for p in sorted(syllabus_dir.glob("*.json")):
            parsed = parse_corpus_name(p)
            if parsed:
                docs.extend(load_syllabus(p, *parsed))
    if bank_dir.is_dir():
        for p in sorted(bank_dir.glob("*.jsonl")):
            parsed = parse_corpus_name(p)
            if parsed:
                docs.extend(load_question_bank(p, *parsed))
    return docs


def kp_labels_from_docs(docs: list[CorpusDocument]) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in docs:
        if d.label and d.kp_id:
            out[d.kp_id] = d.label
    return out


def sync_docs_to_graph(session, docs: list[CorpusDocument]) -> int:
    """Persist corpus documents as PG graph nodes + declared links."""
    count = 0
    seen_tags: set[str] = set()
    seen_links: set[tuple[str, str]] = set()
    for d in docs:
        ntype = d.node_type or d.doc_type
        if ntype == "keyword":
            continue
        upsert_node(
            session,
            node_id=d.node_id,
            node_type=str(ntype),
            subject=d.subject,
            grade_band=d.grade_band,
            label=d.label,
            summary=d.summary,
            full_text=d.full_text or d.text,
            tags_json=d.tags,
            meta_json={**d.meta, "kp_id": d.kp_id, "doc_type": d.doc_type},
        )
        for tag in dict.fromkeys(d.tags):
            tid = tag if tag.startswith("tag:") else f"tag:{tag}"
            if tid not in seen_tags:
                upsert_node(
                    session,
                    node_id=tid,
                    node_type="tag",
                    subject="*",
                    grade_band="*",
                    label=tid,
                )
                seen_tags.add(tid)
            link_key = (d.node_id, tid)
            if link_key in seen_links:
                continue
            seen_links.add(link_key)
            add_edge(session, from_id=d.node_id, to_id=tid, rel_type="tagged_with", source="corpus")
            existing = (
                session.query(CorpusTagLink)
                .filter(CorpusTagLink.node_id == d.node_id, CorpusTagLink.tag_id == tid)
                .first()
            )
            if not existing:
                session.add(CorpusTagLink(node_id=d.node_id, tag_id=tid, created_at=_now_iso()))
        links = d.links or {}
        primary_kp = links.get("primary_kp") or d.kp_id
        if primary_kp and ntype == "question":
            add_edge(session, from_id=d.node_id, to_id=primary_kp, rel_type="covers_kp", source="corpus")
        for kp in links.get("secondary_kp", []):
            add_edge(session, from_id=d.node_id, to_id=kp, rel_type="covers_kp", source="corpus")
        for mid in links.get("methods", links.get("method_ids", [])):
            add_edge(session, from_id=d.node_id, to_id=mid, rel_type="applies_method", source="corpus")
        for sid in links.get("shortcuts", links.get("shortcut_ids", [])):
            add_edge(session, from_id=d.node_id, to_id=sid, rel_type="uses_shortcut_q", source="corpus")
        if ntype == "shortcut":
            for kp in links.get("derived_from_kp", [d.kp_id] if d.kp_id else []):
                add_edge(session, from_id=d.node_id, to_id=kp, rel_type="derived_from_kp", source="corpus")
            for cid in links.get("derived_from_chunk", []):
                add_edge(session, from_id=d.node_id, to_id=cid, rel_type="derived_from_chunk", source="corpus")
        if ntype == "method":
            for kp in links.get("applies_to_kp", [d.kp_id] if d.kp_id else []):
                add_edge(session, from_id=d.node_id, to_id=kp, rel_type="applies_to_kp", source="corpus")
            for sid in links.get("uses_shortcut", links.get("shortcut_ids", [])):
                add_edge(session, from_id=d.node_id, to_id=sid, rel_type="uses_shortcut", source="corpus")
        if ntype == "chunk":
            for kp in links.get("defines_kp", [d.kp_id] if d.kp_id else []):
                add_edge(session, from_id=d.node_id, to_id=kp, rel_type="defines_kp", source="corpus")
            sec = links.get("section_id") or d.parent_id
            if sec:
                add_edge(session, from_id=sec, to_id=d.node_id, rel_type="contains_chunk", source="corpus")
        if ntype == "section":
            tb = links.get("textbook_id") or d.parent_id
            if tb:
                add_edge(session, from_id=tb, to_id=d.node_id, rel_type="contains_section", source="corpus")
            for kp in links.get("covers_kp", [d.kp_id] if d.kp_id else []):
                add_edge(session, from_id=d.node_id, to_id=kp, rel_type="covers_kp", source="corpus")
        count += 1
    session.flush()
    return count
