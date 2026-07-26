from __future__ import annotations

import logging
import re
from collections import defaultdict

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db.models import CorpusEdge, CorpusImportJob, CorpusInboxItem, CorpusNode
from app.schemas.corpus import ReaderPageOut, TreeNodeOut
from app.util.json_util import loads
from app.util.page_sort import page_sort_key

logger = logging.getLogger(__name__)

FOLDER_PREFIX = "folder:kp:"
_PAGE_CHUNK_ID = re.compile(r":p(\d{4})$")


def _tree_meta_for_sidebar(node: CorpusNode) -> dict:
    """Pass only fields the Studio import tree needs for page → preview navigation."""
    raw = loads(node.meta_json, {}) or {}
    out: dict = {}
    for key in (
        "import_job_id",
        "source_page",
        "source_pages",
        "chunk_kind",
        "doc_type",
        "start_page",
        "end_page",
        "block_ids",
        "placeholder",
    ):
        if key in raw and raw[key] is not None:
            out[key] = raw[key]
    return out


def _raw_meta(node: CorpusNode) -> dict:
    return loads(node.meta_json, {}) or {}


def _is_pages_placeholder_section(node: CorpusNode) -> bool:
    """「逐页导入」壳节点 — 仅服务导入流水线，不进教材树。"""
    if node.node_type != "section":
        return False
    if node.id.endswith(":pages"):
        return True
    meta = _raw_meta(node)
    if meta.get("placeholder") is True:
        return True
    return (node.label or "").strip() == "逐页导入"


def _is_page_import_chunk(node: CorpusNode) -> bool:
    if node.node_type != "chunk":
        return False
    meta = _raw_meta(node)
    kind = str(meta.get("chunk_kind") or "")
    if kind == "page_import":
        return True
    if kind in ("toc_leaf", "semantic"):
        return False
    # Legacy page chunks: id …:p0001 without chunk_kind
    return bool(_PAGE_CHUNK_ID.search(node.id or ""))


def _hide_from_textbook_tree(node: CorpusNode) -> bool:
    """Textbook TOC shows sections only — RAG chunks (toc_leaf/semantic/page) stay hidden.

    Leaf sections previously showed a same-label ``toc_leaf`` child; that duplicated
    the parent in the sidebar. Chunks remain in the graph for retrieval.
    """
    if _is_pages_placeholder_section(node):
        return True
    if node.node_type == "chunk":
        return True
    return False


def _source_page_of(node: CorpusNode) -> int | None:
    meta = _raw_meta(node)
    sp = meta.get("source_page")
    if sp is not None:
        try:
            n = int(sp)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    pages = meta.get("source_pages")
    if isinstance(pages, list) and pages:
        try:
            n = int(pages[0])
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    start = meta.get("start_page")
    if start is not None:
        try:
            n = int(start)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    m = _PAGE_CHUNK_ID.search(node.id or "")
    if m:
        return int(m.group(1))
    return None


def _tree_node(
    *,
    id: str,
    label: str,
    node_type: str,
    status: str = "",
    child_count: int = 0,
    has_children: bool = False,
    meta_hint: str = "",
    meta: dict | None = None,
) -> TreeNodeOut:
    return TreeNodeOut(
        id=id,
        label=label or id,
        node_type=node_type,
        status=status,
        child_count=child_count,
        has_children=has_children,
        meta_hint=meta_hint,
        meta=meta or {},
    )


def _tree_sort_key(node: TreeNodeOut) -> tuple[int, int, str]:
    meta = node.meta or {}
    source_page = meta.get("source_page")
    if source_page is None:
        source_page = meta.get("start_page")
    if source_page is None:
        pages = meta.get("source_pages")
        if isinstance(pages, list) and pages:
            source_page = pages[0]
    return page_sort_key(node_id=node.id, label=node.label, source_page=source_page)


def _children_via_edge(
    session: Session,
    parent_id: str,
    rel_type: str,
    *,
    subject: str | None = None,
    grade_band: str | None = None,
) -> list[TreeNodeOut]:
    edges = (
        session.query(CorpusEdge)
        .filter(CorpusEdge.from_id == parent_id, CorpusEdge.rel_type == rel_type)
        .all()
    )
    out: list[TreeNodeOut] = []
    for e in edges:
        node = session.get(CorpusNode, e.to_id)
        if node is None:
            continue
        if subject and node.subject not in (subject, "*"):
            continue
        if grade_band and node.grade_band not in (grade_band, "*"):
            continue
        child_rel = _child_rel_for(node.node_type)
        child_count = 0
        if node.node_type == "section":
            for rel in ("contains_section", "contains_chunk"):
                child_count += (
                    session.query(func.count(CorpusEdge.id))
                    .filter(CorpusEdge.from_id == node.id, CorpusEdge.rel_type == rel)
                    .scalar()
                    or 0
                )
        elif child_rel:
            child_count = (
                session.query(func.count(CorpusEdge.id))
                .filter(CorpusEdge.from_id == node.id, CorpusEdge.rel_type == child_rel)
                .scalar()
                or 0
            )
        if _hide_from_textbook_tree(node):
            continue
        # Recount children excluding hidden page-import / 逐页导入.
        visible_count = child_count
        if node.node_type == "section":
            visible_count = _visible_section_child_count(session, node.id)
        out.append(
            _tree_node(
                id=node.id,
                label=node.label,
                node_type=node.node_type,
                status=node.status,
                child_count=int(visible_count),
                has_children=(
                    visible_count > 0
                    if node.node_type == "section"
                    else visible_count > 0 or node.node_type in ("textbook", "kp")
                ),
                meta=_tree_meta_for_sidebar(node),
            )
        )
    out.sort(key=_tree_sort_key)
    return out


def _visible_section_child_count(session: Session, section_id: str) -> int:
    n = 0
    for rel in ("contains_section", "contains_chunk"):
        edges = (
            session.query(CorpusEdge)
            .filter(CorpusEdge.from_id == section_id, CorpusEdge.rel_type == rel)
            .all()
        )
        for e in edges:
            child = session.get(CorpusNode, e.to_id)
            if child is None or _hide_from_textbook_tree(child):
                continue
            n += 1
    return n


def _section_children(session: Session, parent_id: str, *, subject: str | None, grade_band: str | None) -> list[TreeNodeOut]:
    seen: set[str] = set()
    out: list[TreeNodeOut] = []
    for rel in ("contains_section", "contains_chunk"):
        for node in _children_via_edge(session, parent_id, rel, subject=subject, grade_band=grade_band):
            if node.id in seen:
                continue
            seen.add(node.id)
            out.append(node)
    out.sort(key=_tree_sort_key)
    return out


def _import_job_page_children(session: Session, job: CorpusImportJob) -> list[TreeNodeOut]:
    """Import ribbon: flat PDF pages only (1, 2, 3…), no TOC / semantic chunks."""
    tb_id = (job.textbook_node_id or "").strip()
    if not tb_id:
        return []
    prefix = f"chunk:{tb_id}:p"
    rows = (
        session.query(CorpusNode)
        .filter(CorpusNode.node_type == "chunk", CorpusNode.id.like(f"{prefix}%"))
        .all()
    )
    out: list[TreeNodeOut] = []
    for node in rows:
        meta = _raw_meta(node)
        kind = str(meta.get("chunk_kind") or "")
        if kind and kind != "page_import":
            continue
        page = _source_page_of(node)
        if not page:
            m = _PAGE_CHUNK_ID.search(node.id or "")
            page = int(m.group(1)) if m else 0
        if page <= 0:
            continue
        side_meta = _tree_meta_for_sidebar(node)
        side_meta["source_page"] = page
        side_meta["chunk_kind"] = "page_import"
        if job.id:
            side_meta["import_job_id"] = job.id
        out.append(
            _tree_node(
                id=node.id,
                label=str(page),
                node_type="chunk",
                status=node.status,
                has_children=False,
                meta=side_meta,
            )
        )
    out.sort(key=_tree_sort_key)
    return out


def list_textbook_reader_pages(session: Session, node_id: str) -> list[ReaderPageOut]:
    """Ordered PDF pages for continuous textbook reader (center scroll)."""
    node = session.get(CorpusNode, node_id)
    if node is None:
        return []
    meta = _raw_meta(node)
    links = meta.get("links") or {}
    if node.node_type == "textbook":
        tb_id = node.id
    else:
        tb_id = str(links.get("textbook_id") or "")
    if not tb_id:
        return []

    prefix = f"chunk:{tb_id}:p"
    rows = (
        session.query(CorpusNode)
        .filter(CorpusNode.node_type == "chunk", CorpusNode.id.like(f"{prefix}%"))
        .all()
    )
    pages: list[ReaderPageOut] = []
    for row in rows:
        rmeta = _raw_meta(row)
        kind = str(rmeta.get("chunk_kind") or "")
        if kind and kind != "page_import":
            continue
        page = _source_page_of(row)
        if not page:
            m = _PAGE_CHUNK_ID.search(row.id or "")
            page = int(m.group(1)) if m else 0
        if page <= 0:
            continue
        rlinks = rmeta.get("links") or {}
        section_id = str(rlinks.get("section_id") or links.get("section_id") or "")
        # Prefer section other than the pages placeholder shell.
        if section_id.endswith(":pages"):
            section_id = ""
        pages.append(
            ReaderPageOut(
                id=row.id,
                label=row.label or f"第 {page} 页",
                source_page=page,
                section_id=section_id,
                status=row.status or "",
                import_job_id=str(rmeta.get("import_job_id") or ""),
            )
        )
    pages.sort(key=lambda p: p.source_page)
    return pages


def _child_rel_for(node_type: str) -> str | None:
    return {
        "textbook": "contains_section",
        "section": "contains_section",
    }.get(node_type)


def _textbook_roots(session: Session, subject: str | None, grade_band: str | None) -> list[TreeNodeOut]:
    q = session.query(CorpusNode).filter(CorpusNode.node_type == "textbook")
    if subject:
        q = q.filter(CorpusNode.subject == subject)
    if grade_band:
        q = q.filter(CorpusNode.grade_band == grade_band)
    rows = q.order_by(CorpusNode.label).all()
    out: list[TreeNodeOut] = []
    for node in rows:
        child_count = 0
        for e in (
            session.query(CorpusEdge)
            .filter(CorpusEdge.from_id == node.id, CorpusEdge.rel_type == "contains_section")
            .all()
        ):
            child = session.get(CorpusNode, e.to_id)
            if child is None or _hide_from_textbook_tree(child):
                continue
            child_count += 1
        out.append(
            _tree_node(
                id=node.id,
                label=node.label,
                node_type="textbook",
                status=node.status,
                child_count=int(child_count),
                has_children=child_count > 0,
                meta=_tree_meta_for_sidebar(node),
            )
        )
    return out


def _is_syllabus_kp(node: CorpusNode) -> bool:
    if node.id.startswith("kw:"):
        return False
    meta = loads(node.meta_json, {})
    doc_type = meta.get("doc_type", "")
    if doc_type == "keyword":
        return False
    return node.node_type == "kp"


def _kp_roots(session: Session, subject: str | None, grade_band: str | None) -> list[TreeNodeOut]:
    q = session.query(CorpusNode).filter(CorpusNode.node_type == "kp")
    if subject:
        q = q.filter(or_(CorpusNode.subject == subject, CorpusNode.subject == "*"))
    if grade_band:
        q = q.filter(or_(CorpusNode.grade_band == grade_band, CorpusNode.grade_band == "*"))
    rows = q.all()
    out: list[TreeNodeOut] = []
    for node in rows:
        if not _is_syllabus_kp(node):
            continue
        meta = loads(node.meta_json, {})
        level = int(meta.get("level", 0) or 0)
        if level != 1:
            continue
        child_count = sum(
            1
            for n in rows
            if _is_syllabus_kp(n)
            and n.id.startswith(node.id + ".")
            and int(loads(n.meta_json, {}).get("level", 0) or 0) == level + 1
            and "." not in n.id[len(node.id) + 1 :]
        )
        out.append(
            _tree_node(
                id=node.id,
                label=node.label,
                node_type="kp",
                status=node.status,
                child_count=child_count,
                has_children=child_count > 0,
                meta_hint=meta.get("label_en", ""),
            )
        )
    out.sort(key=lambda x: x.id)
    return out


def _kp_children(session: Session, parent_id: str) -> list[TreeNodeOut]:
    parent = session.get(CorpusNode, parent_id)
    parent_level = 1
    if parent:
        parent_level = int(loads(parent.meta_json, {}).get("level", 1) or 1)
    q = session.query(CorpusNode).filter(
        CorpusNode.node_type == "kp",
        CorpusNode.id.like(f"{parent_id}.%"),
    )
    rows = q.all()
    out: list[TreeNodeOut] = []
    for node in rows:
        if not _is_syllabus_kp(node):
            continue
        suffix = node.id[len(parent_id) + 1 :]
        if "." in suffix:
            continue
        meta = loads(node.meta_json, {})
        level = int(meta.get("level", 0) or 0)
        if level != parent_level + 1:
            continue
        child_count = sum(
            1
            for n in rows
            if _is_syllabus_kp(n)
            and n.id.startswith(node.id + ".")
            and "." not in n.id[len(node.id) + 1 :]
        )
        out.append(
            _tree_node(
                id=node.id,
                label=node.label,
                node_type="kp",
                status=node.status,
                child_count=child_count,
                has_children=child_count > 0,
                meta_hint=meta.get("label_en", ""),
            )
        )
    out.sort(key=lambda x: x.id)
    return out


def _group_by_kp_folders(
    session: Session,
    node_type: str,
    *,
    subject: str | None,
    grade_band: str | None,
) -> list[TreeNodeOut]:
    q = session.query(CorpusNode).filter(CorpusNode.node_type == node_type)
    if subject:
        q = q.filter(CorpusNode.subject == subject)
    if grade_band:
        q = q.filter(CorpusNode.grade_band == grade_band)
    rows = q.all()
    groups: dict[str, list[CorpusNode]] = defaultdict(list)
    for node in rows:
        meta = loads(node.meta_json, {})
        links = meta.get("links") or {}
        kp_id = meta.get("kp_id") or links.get("primary_kp") or "未分类"
        groups[str(kp_id)].append(node)

    out: list[TreeNodeOut] = []
    for kp_id, nodes in sorted(groups.items(), key=lambda x: x[0]):
        folder_id = f"{FOLDER_PREFIX}{kp_id}"
        kp_node = session.get(CorpusNode, kp_id) if not kp_id.startswith("未") else None
        label = kp_node.label if kp_node else kp_id
        out.append(
            _tree_node(
                id=folder_id,
                label=label,
                node_type="folder",
                child_count=len(nodes),
                has_children=len(nodes) > 0,
                meta_hint=node_type,
            )
        )
    return out


def _folder_children(
    session: Session,
    folder_id: str,
    node_type: str,
    *,
    subject: str | None,
    grade_band: str | None,
) -> list[TreeNodeOut]:
    if not folder_id.startswith(FOLDER_PREFIX):
        return []
    kp_id = folder_id[len(FOLDER_PREFIX) :]
    q = session.query(CorpusNode).filter(CorpusNode.node_type == node_type)
    if subject:
        q = q.filter(CorpusNode.subject == subject)
    if grade_band:
        q = q.filter(CorpusNode.grade_band == grade_band)
    rows = q.all()
    out: list[TreeNodeOut] = []
    for node in rows:
        meta = loads(node.meta_json, {})
        links = meta.get("links") or {}
        node_kp = meta.get("kp_id") or links.get("primary_kp") or "未分类"
        if str(node_kp) != kp_id:
            continue
        out.append(
            _tree_node(
                id=node.id,
                label=node.label or node.id,
                node_type=node.node_type,
                status=node.status,
                has_children=False,
            )
        )
    out.sort(key=_tree_sort_key)
    return out


def _import_roots(session: Session) -> list[TreeNodeOut]:
    rows = (
        session.query(CorpusImportJob)
        .order_by(CorpusImportJob.created_at.desc())
        .limit(50)
        .all()
    )
    out: list[TreeNodeOut] = []
    for job in rows:
        label = job.label or job.id[:8]
        nodes_missing = False
        if job.textbook_node_id and job.status in ("chunked", "review"):
            nodes_missing = session.get(CorpusNode, job.textbook_node_id) is None
        if nodes_missing:
            label = f"{label}（节点已丢失）"
        out.append(
            _tree_node(
                id=f"job:{job.id}",
                label=label,
                node_type="import_job",
                status=job.status,
                has_children=bool(job.textbook_node_id) and not nodes_missing,
                meta_hint=job.textbook_node_id,
            )
        )
    return out


def _inbox_roots(session: Session) -> list[TreeNodeOut]:
    rows = (
        session.query(CorpusInboxItem.status, func.count(CorpusInboxItem.id))
        .group_by(CorpusInboxItem.status)
        .all()
    )
    return [
        _tree_node(
            id=f"inbox:{status}",
            label=f"{status} ({count})",
            node_type="inbox_group",
            status=str(status),
            child_count=int(count),
            has_children=int(count) > 0,
        )
        for status, count in rows
    ]


def _inbox_children(session: Session, group_id: str) -> list[TreeNodeOut]:
    if not group_id.startswith("inbox:"):
        return []
    status = group_id[len("inbox:") :]
    rows = (
        session.query(CorpusInboxItem)
        .filter(CorpusInboxItem.status == status)
        .order_by(CorpusInboxItem.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        _tree_node(
            id=f"inbox-item:{item.id}",
            label=item.title or item.id[:8],
            node_type="inbox_item",
            status=item.status,
            meta_hint=item.source_platform,
        )
        for item in rows
    ]


def get_tree(
    session: Session,
    *,
    root: str,
    parent_id: str = "",
    subject: str | None = None,
    grade_band: str | None = None,
    depth: int = 1,
) -> list[TreeNodeOut]:
    """Lazy tree navigation for Corpus Studio vault sidebar."""
    if parent_id:
        if parent_id.startswith(FOLDER_PREFIX):
            hint = ""
            for n in _group_by_kp_folders(session, "method", subject=subject, grade_band=grade_band):
                if n.id == parent_id:
                    hint = "method"
                    break
            if not hint:
                for n in _group_by_kp_folders(session, "shortcut", subject=subject, grade_band=grade_band):
                    if n.id == parent_id:
                        hint = "shortcut"
                        break
            if not hint:
                for n in _group_by_kp_folders(session, "question", subject=subject, grade_band=grade_band):
                    if n.id == parent_id:
                        hint = "question"
                        break
            if hint:
                return _folder_children(session, parent_id, hint, subject=subject, grade_band=grade_band)

        if parent_id.startswith("inbox:"):
            return _inbox_children(session, parent_id)

        node = session.get(CorpusNode, parent_id)
        if node:
            if node.node_type == "textbook":
                return _children_via_edge(session, parent_id, "contains_section", subject=subject, grade_band=grade_band)
            if node.node_type == "section":
                return _section_children(session, parent_id, subject=subject, grade_band=grade_band)
            if node.node_type == "kp":
                return _kp_children(session, parent_id)

        if parent_id.startswith("job:"):
            job_id = parent_id[4:]
            job = session.get(CorpusImportJob, job_id)
            if job:
                return _import_job_page_children(session, job)
        return []

    if root == "textbook":
        return _textbook_roots(session, subject, grade_band)
    if root == "kp":
        return _kp_roots(session, subject, grade_band)
    if root == "method":
        return _group_by_kp_folders(session, "method", subject=subject, grade_band=grade_band)
    if root == "shortcut":
        return _group_by_kp_folders(session, "shortcut", subject=subject, grade_band=grade_band)
    if root == "question":
        return _group_by_kp_folders(session, "question", subject=subject, grade_band=grade_band)
    if root == "import":
        return _import_roots(session)
    if root == "inbox":
        return _inbox_roots(session)
    return []


def collect_subtree_ids(session: Session, root_id: str) -> list[str]:
    """BFS collect node ids under textbook/section via contains_* edges."""
    node = session.get(CorpusNode, root_id)
    if node is None:
        return []
    collected: list[str] = [root_id]
    frontier = [root_id]
    rel_map = {
        "textbook": ("contains_section",),
        "section": ("contains_section", "contains_chunk"),
    }
    while frontier:
        next_frontier: list[str] = []
        for pid in frontier:
            pnode = session.get(CorpusNode, pid)
            if pnode is None:
                continue
            rels = rel_map.get(pnode.node_type)
            if not rels:
                continue
            edges = (
                session.query(CorpusEdge)
                .filter(CorpusEdge.from_id == pid, CorpusEdge.rel_type.in_(rels))
                .all()
            )
            for e in edges:
                if e.to_id not in collected:
                    collected.append(e.to_id)
                    child = session.get(CorpusNode, e.to_id)
                    if child and child.node_type in rel_map:
                        next_frontier.append(e.to_id)
        frontier = next_frontier
    return collected
