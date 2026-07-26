"""v4 反馈面：裁决回流 —— override 叠加 + 确定性重编译（走管线②③，无 LLM）。

原则：
- ``blueprints/pages.json``（模型输出 + 防腐层修复 + normalize 后的落盘）
  永远不改——它是「事实」；用户修正进 ``import35b_overrides`` 叠加层。
- 每次重编译从落盘 blueprint 出发重放**全部** override，幂等、可撤销。
- 重编译后重跑 T1 校验，审核队列同步刷新。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.db.models import Import35bJob
from app.import_35b import store
from app.import_35b.assets import asset_prefix_from_minio_key
from app.import_35b.page_compiler import compile_page_html
from app.import_35b.render_zone import html_to_plain_text
from app.import_35b.schemas import PageBlueprint
from app.import_35b.validation import run_page_checks
from app.util.json_util import loads

logger = logging.getLogger(__name__)


def _load_page_blueprint_dict(job: Import35bJob, page_no: int) -> dict[str, Any] | None:
    """Load one page blueprint from MinIO.

    Lookup order (first hit wins):
    1. ``{assets}/35b/blueprints/p{NNNN}.json`` — per-page artifact (mid-run + final)
    2. ``job.meta.blueprints_key`` or ``{assets}/35b/blueprints/pages.json`` aggregate
    3. ``{assets}/35b/debug/p{NNNN}/blueprint.json`` — partial/failed runs
    """
    prefix = asset_prefix_from_minio_key(job.minio_key).rstrip("/")
    page_tag = f"p{page_no:04d}"

    per_page_key = f"{prefix}/35b/blueprints/{page_tag}.json"
    bp = _read_blueprint_json(per_page_key)
    if bp is not None:
        return bp

    meta = loads(getattr(job, "meta_json", None) or "{}", {})
    aggregate_keys: list[str] = []
    if meta.get("blueprints_key"):
        aggregate_keys.append(str(meta["blueprints_key"]))
    aggregate_keys.append(f"{prefix}/35b/blueprints/pages.json")
    seen: set[str] = set()
    for key in aggregate_keys:
        if key in seen:
            continue
        seen.add(key)
        bp = _page_from_blueprints_aggregate(key, page_no)
        if bp is not None:
            return bp

    debug_key = f"{prefix}/35b/debug/{page_tag}/blueprint.json"
    return _read_blueprint_json(debug_key)


def _read_blueprint_json(key: str) -> dict[str, Any] | None:
    data = get_object_bytes(key)
    if not data:
        return None
    try:
        obj = json.loads(data)
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def _page_from_blueprints_aggregate(key: str, page_no: int) -> dict[str, Any] | None:
    data = get_object_bytes(key)
    if not data:
        return None
    try:
        pages = json.loads(data)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(pages, dict):
        if int(pages.get("page_no") or -1) == page_no:
            return pages
        return None
    if not isinstance(pages, list):
        return None
    for p in pages:
        if isinstance(p, dict) and int(p.get("page_no") or -1) == page_no:
            return p
    return None


def _apply_override_to_block(block: dict[str, Any], target: str, payload: dict) -> int:
    """块内确定性替换：old 精确匹配才替换。返回命中次数。"""
    old = str(payload.get("old") or "")
    new = str(payload.get("new") or "")
    if not old or old == new:
        return 0
    hits = 0
    content = block.get("content") or {}

    segments = content.get("text_segments") or []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        if target == "latex" and seg.get("kind") == "math":
            if seg.get("latex") == old or seg.get("raw") in (old, f"${old}$"):
                seg["latex"] = new
                seg["valid"] = True
                hits += 1
        elif target == "text" and seg.get("kind") == "text" and old in str(seg.get("raw") or ""):
            seg["raw"] = str(seg["raw"]).replace(old, new)
            hits += 1

    eq = content.get("equation")
    if isinstance(eq, dict):
        if target == "latex" and eq.get("latex") == old:
            eq["latex"] = new
            hits += 1

    text = content.get("text")
    if isinstance(text, dict) and target == "text":
        paras = text.get("paragraphs")
        if isinstance(paras, list):
            for i, p in enumerate(paras):
                if old in str(p):
                    paras[i] = str(p).replace(old, new)
                    hits += 1

    table = content.get("table")
    if isinstance(table, dict):
        rows = table.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, list):
                    continue
                for i, cell in enumerate(row):
                    if old in str(cell):
                        row[i] = str(cell).replace(old, new)
                        hits += 1

    fig = content.get("figure")
    if isinstance(fig, dict) and target == "text":
        cap = str(fig.get("caption") or "")
        if old in cap:
            fig["caption"] = cap.replace(old, new)
            hits += 1

    return hits


def recompile_page(session: Session, job: Import35bJob, page_no: int) -> str:
    """落盘 blueprint + 全量 override 重放 → 重编译 → 更新页 + 刷新审核队列。"""
    bp_dict = _load_page_blueprint_dict(job, page_no)
    if bp_dict is None:
        raise ValueError(f"blueprint for page {page_no} not found in MinIO")

    applied = 0
    for ov in store.get_overrides(session, job.textbook_key, page_no):
        payload = loads(ov.payload_json, {})
        for block in bp_dict.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if ov.block_id and str(block.get("id") or "") != ov.block_id:
                continue
            applied += _apply_override_to_block(block, ov.target, payload)

    bp = PageBlueprint.model_validate(bp_dict)
    ssr_report: list[dict] = []
    # 落盘 blueprint 已 normalize（segments 在位），直接编译，不再重分段
    html = compile_page_html(bp, profile_id=job.profile_id or "hujiao_math_hs_v1",
                             ssr_report=ssr_report)

    store.upsert_page(
        session,
        textbook_key=job.textbook_key,
        job_id=job.id,
        page_no=page_no,
        html=html,
        plain_text=html_to_plain_text(html),
    )

    try:
        findings = [f.model_dump() for f in run_page_checks(bp, html, ssr_report=ssr_report)]
        try:
            from app.import_35b.caselaw import enrich_findings

            findings = enrich_findings(
                session,
                findings,
                subject=job.subject or "math",
                grade_band=job.grade_band or "hs",
            )
        except Exception:  # noqa: BLE001 — 富化绝不阻断重编译
            logger.exception("import35b recompile caselaw enrich failed page=%d", page_no)
        store.save_review_items(
            session,
            job_id=job.id,
            textbook_key=job.textbook_key,
            page_no=page_no,
            findings=findings,
        )
    except Exception:  # noqa: BLE001
        logger.exception("import35b recompile validation failed page=%d", page_no)

    logger.info(
        "import35b recompiled page=%d overrides_applied=%d tb=%s",
        page_no,
        applied,
        job.textbook_key,
    )
    return html
