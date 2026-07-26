from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.db.models import CorpusInboxItem, CorpusScoutRun
from app.services.corpus_publish import upsert_draft_node
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

_BILIBILI_RE = re.compile(r"bilibili\.com/video/(BV[\w]+)", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_platform(url: str) -> str:
    if "bilibili.com" in url:
        return "bilibili"
    if url.startswith("http"):
        return "web"
    return "unknown"


def _fetch_url_text(url: str, timeout: float = 30.0) -> tuple[str, str]:
    """Best-effort fetch title + snippet from URL."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, headers={"User-Agent": "LearningBuddy-Scout/1.0"})
            r.raise_for_status()
            html = r.text[:50000]
            title_m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
            title = title_m.group(1).strip() if title_m else url
            snippet = re.sub(r"<[^>]+>", " ", html)[:2000]
            snippet = re.sub(r"\s+", " ", snippet).strip()
            return title, snippet
    except Exception as e:
        logger.warning("scout fetch failed url=%s: %s", url, e)
        return url, ""


def create_scout_run(
    session: Session,
    *,
    query: str = "",
    url: str = "",
    subject: str = "math",
    grade_band: str = "hs",
) -> CorpusScoutRun:
    run = CorpusScoutRun(
        id=str(uuid.uuid4()),
        query=query,
        url=url,
        subject=subject,
        grade_band=grade_band,
        status="running",
        started_at=_now_iso(),
    )
    session.add(run)
    session.flush()
    return run


def run_scout_sync(session: Session, run_id: str) -> CorpusScoutRun:
    run = session.get(CorpusScoutRun, run_id)
    if run is None:
        raise ValueError(f"scout run not found: {run_id}")
    steps: list[dict] = []

    try:
        target_url = run.url.strip()
        if not target_url and run.query.strip().startswith("http"):
            target_url = run.query.strip()

        title = run.query or "Scout resource"
        snippet = ""
        platform = "query"
        resource_kind = "article"

        if target_url:
            platform = _detect_platform(target_url)
            title, snippet = _fetch_url_text(target_url)
            resource_kind = "video" if platform == "bilibili" else "article"
            steps.append({"node": "fetch", "url": target_url, "title": title})

        if platform == "bilibili":
            bvid_m = _BILIBILI_RE.search(target_url)
            bvid = bvid_m.group(1) if bvid_m else ""
            node_id = f"media:bilibili:{bvid or uuid.uuid4().hex[:8]}"
            summary = snippet[:500] or title
            upsert_draft_node(
                session,
                node_id=node_id,
                node_type="media",
                subject=run.subject,
                grade_band=run.grade_band,
                label=title,
                summary=summary,
                full_text=snippet,
                tags=[f"grade_band:{run.grade_band}", "platform:bilibili"],
                links={},
                meta={
                    "doc_type": "media",
                    "url": target_url,
                    "platform": "bilibili",
                    "bvid": bvid,
                    "media_type": "video",
                    "transcript": snippet,
                    "license": "unknown",
                },
                source_channel="bilibili",
            )
        else:
            slug = re.sub(r"[^\w]+", "-", title.lower())[:24] or uuid.uuid4().hex[:8]
            node_id = f"chunk:scout:{run.subject}:{slug}"
            upsert_draft_node(
                session,
                node_id=node_id,
                node_type="chunk",
                subject=run.subject,
                grade_band=run.grade_band,
                label=title,
                summary=snippet[:240] or title,
                full_text=snippet or title,
                tags=[f"grade_band:{run.grade_band}", f"platform:{platform}"],
                links={},
                meta={"doc_type": "chunk", "source_url": target_url},
                source_channel="scout",
            )

        inbox = CorpusInboxItem(
            id=str(uuid.uuid4()),
            scout_run_id=run.id,
            status="ready",
            source_url=target_url,
            source_platform=platform,
            resource_kind=resource_kind,
            subject=run.subject,
            grade_band=run.grade_band,
            title=title,
            snippet=snippet[:500],
            proposed_node_ids_json=dumps([node_id]),
            agent_confidence=0.7 if snippet else 0.4,
            agent_notes_json=dumps({"warnings": ["license: unknown"] if platform != "manual" else []}),
            created_at=_now_iso(),
        )
        session.add(inbox)
        steps.append({"node": "write_inbox", "node_id": node_id})

        run.status = "succeeded"
        run.inbox_count = 1
        run.steps_json = dumps(steps)
        run.finished_at = _now_iso()
        session.flush()
        return run
    except Exception as e:
        run.status = "failed"
        run.error = str(e)
        run.steps_json = dumps(steps + [{"node": "error", "detail": str(e)}])
        run.finished_at = _now_iso()
        session.flush()
        raise


def approve_inbox_item(session: Session, item_id: str, *, publish: bool = True) -> list[str]:
    from app.db.models import CorpusNode
    from app.services.corpus_publish import publish_node

    item = session.get(CorpusInboxItem, item_id)
    if item is None:
        raise ValueError(f"inbox item not found: {item_id}")
    item.status = "approved"
    item.reviewed_at = _now_iso()
    node_ids = loads(item.proposed_node_ids_json, [])
    if publish:
        from app.services.corpus_governance import submit_for_review

        for nid in node_ids:
            node = session.get(CorpusNode, nid)
            if node and node.status == "draft":
                submit_for_review(session, nid)
                publish_node(session, nid)
    session.flush()
    return node_ids
