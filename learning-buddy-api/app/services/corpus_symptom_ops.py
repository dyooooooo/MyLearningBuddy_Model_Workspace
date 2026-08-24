"""症状检测器的运维面：试跑、正反例回归、误报记账、晋级门。

抽象轨的 Agent 靠这四件事自主收敛：写完一份检测器先在真实页上试跑看命中什么，
再跑正反例量召回和误报，不达标就回去改 YAML。改的是挂载目录，下一次调用就生效，
不用重建镜像，也不用等人。

这些操作全都**不改检测器文件**——写文件是 Agent 自己的事，这里只负责回答
「你写的这条到底准不准」。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    CorpusImportJob,
    CorpusImprovementPattern,
    CorpusImprovementPoint,
    CorpusSymptomFalsePositive,
)
from app.services.corpus_symptoms import (
    CHECKS_DIR,
    SymptomCheck,
    load_check_source,
    load_checks,
    load_errors,
    run_checks_on_page,
    vocabulary_reference,
)
from app.services.document_ir import DocumentBlock
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

_API_ROOT = Path(__file__).resolve().parents[2]

# 晋级门：playbook 升 rule 的硬指标。低于这个就不是规律，是巧合。
PROMOTE_MIN_MEMBERS = 3
PROMOTE_MIN_BOOKS = 2
# 误报率超过这条线，检测器自动停用建议：太吵的检测器比没有更糟。
MAX_FALSE_POSITIVE_RATE = 0.2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_check(suspect_type: str = "", yaml_text: str = "") -> SymptomCheck:
    """要么给一段还没落盘的 YAML（试跑），要么给一个已发布的症状名。"""
    if yaml_text.strip():
        return load_check_source(yaml_text, source="<dry-run>")
    wanted = (suspect_type or "").strip().upper()
    for check in load_checks():
        if check.suspect_type == wanted:
            return check
    raise ValueError(f"检测器不存在：{wanted or '(未指定)'}")


# ── 试跑：在真实页上看命中什么 ──────────────────────────────────────────────


def dry_run(
    session: Session,
    *,
    job_id: str,
    suspect_type: str = "",
    yaml_text: str = "",
    pages: list[int] | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    """在一次导入的真实页上跑一条检测器。不落库，不改文件。

    ``pages`` 留空就扫全书。命中列表带证据，直接就是「该不该报」的判断材料。
    """
    from app.services.import_ir_pipeline import get_job_ir, rerender_page_html
    from app.services.ir.textbook_profile import default_profile_id

    check = _resolve_check(suspect_type, yaml_text)
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")

    blocks = get_job_ir(job) or []
    if not blocks:
        raise ValueError("这次导入没有中间表示（IR）块，无法试跑")

    meta = loads(job.meta_json, {}) or {}
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    wanted = sorted({int(p) for p in (pages or [])}) or sorted({b.page_no for b in blocks})

    hits: list[dict[str, Any]] = []
    scanned = 0
    for page_no in wanted:
        page_blocks = [b for b in blocks if b.page_no == page_no]
        if not page_blocks:
            continue
        scanned += 1
        html = rerender_page_html(blocks, page_no, profile_id=profile_id)
        for hit in run_checks_on_page(blocks, html, page_no=page_no, checks=[check]):
            hits.append(hit.model_dump())
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break

    hit_pages = sorted({int(h["page_no"]) for h in hits})
    return {
        "ok": True,
        "suspect_type": check.suspect_type,
        "scanned_pages": scanned,
        "hit_count": len(hits),
        "hit_pages": hit_pages,
        "truncated": len(hits) >= limit,
        "hits": hits,
    }


# ── 正反例回归：量召回率与误报率 ───────────────────────────────────────────


def _load_example(rel: str) -> tuple[list[DocumentBlock], str, int]:
    path = _API_ROOT / rel
    if not path.is_file():
        raise ValueError(f"样例文件不存在：{rel}")
    data = json.loads(path.read_text(encoding="utf-8"))
    page_no = int(data.get("page_no") or 1)
    html = str(data.get("html") or "<article></article>")
    blocks = [DocumentBlock.model_validate(b) for b in data.get("blocks") or []]
    return blocks, html, page_no


def run_examples(*, suspect_type: str = "", yaml_text: str = "") -> dict[str, Any]:
    """跑一条检测器的正反例，给出召回率和误报率。

    正例召回不满 100% 或反例有任何误报，就 ``passed: false``——这条检测器还不能
    发布成 ``rule`` 档，最多降级为 ``playbook``。
    """
    check = _resolve_check(suspect_type, yaml_text)
    detail: list[dict[str, Any]] = []

    recalled = 0
    for rel in check.examples.positive:
        blocks, html, page_no = _load_example(rel)
        hit = bool(run_checks_on_page(blocks, html, page_no=page_no, checks=[check]))
        recalled += int(hit)
        detail.append({"file": rel, "polarity": "positive", "hit": hit, "ok": hit})

    false_hits = 0
    for rel in check.examples.negative:
        blocks, html, page_no = _load_example(rel)
        hit = bool(run_checks_on_page(blocks, html, page_no=page_no, checks=[check]))
        false_hits += int(hit)
        detail.append({"file": rel, "polarity": "negative", "hit": hit, "ok": not hit})

    n_pos = len(check.examples.positive)
    n_neg = len(check.examples.negative)
    recall = (recalled / n_pos) if n_pos else 0.0
    fp_rate = (false_hits / n_neg) if n_neg else 0.0
    passed = bool(n_pos and n_neg and recalled == n_pos and false_hits == 0)
    return {
        "ok": True,
        "suspect_type": check.suspect_type,
        "passed": passed,
        "recall": round(recall, 3),
        "false_positive_rate": round(fp_rate, 3),
        "positive_count": n_pos,
        "negative_count": n_neg,
        "detail": detail,
        "verdict": (
            "可发布为 rule 档"
            if passed
            else "召回不满或有误报，只能降级为 playbook 档" if (n_pos and n_neg)
            else "缺正例或反例，没测过就不算数"
        ),
    }


def _hits_by_suspect(session: Session | None) -> dict[str, dict[str, int]] | None:
    """每个症状名累计命中多少处、其中多少被判为误报。

    词表体检要靠它把「这个词实际起了多少作用」算出来：一个词只被一条从没命中过的
    检测器引用，和被一条命中几百处的检测器引用，维护价值差得很远。
    """
    if session is None:
        return None
    try:
        rows = session.query(CorpusImprovementPattern).all()
    except Exception as exc:  # noqa: BLE001 — 诊断拿不到就不给，别拖垮清单
        logger.warning("hits by suspect unavailable: %s", exc)
        return None
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        suspect = str(row.suspect_type or "")
        if not suspect:
            continue
        stats = out.setdefault(suspect, {"hits": 0, "false_positive": 0})
        stats["hits"] += int(row.hits or 0)
        stats["false_positive"] += int(row.false_positive or 0)
    return out


def checks_overview(session: Session | None = None) -> dict[str, Any]:
    """当前生效的检测器清单，附词表和没能生效的那些。

    ``load_errors`` 是这份清单里最该先看的一段：写坏的声明不会出现在 ``checks``
    里，只看 ``checks`` 会以为一切正常，实际上那条检测器根本没加载。

    给了 ``session`` 就顺带把词表的命中量诊断算上（哪个词真在起作用、哪个是孤词）。
    """
    checks = load_checks()
    errors = load_errors()
    return {
        "ok": True,
        "checks_dir": str(CHECKS_DIR),
        "count": len(checks),
        "load_errors": [e.model_dump() for e in errors],
        "checks": [
            {
                "suspect_type": c.suspect_type,
                "title": c.title,
                "severity": c.severity,
                "enabled": c.enabled,
                "scope": c.detect.scope,
                "when": c.detect.when,
                "min_hits": c.detect.min_hits,
                "evidence": list(c.evidence),
                # 定位字段：改进器只拿这个子集，界面上要看得见「凭什么定位」
                "locate_by": c.remediation.locate_fields(c.evidence),
                "remediation_kind": c.remediation.kind,
                "landing_target": c.remediation.landing_target,
                # 手册步骤全文：Agent 要照着做，给路径等于没给。
                "playbook": c.remediation.playbook,
                "rule_ref": c.remediation.rule_ref,
                "has_examples": bool(c.examples.positive and c.examples.negative),
                # 样例路径本身就是证据：没有样例的检测器等于没测过。
                "examples": {
                    "positive": list(c.examples.positive),
                    "negative": list(c.examples.negative),
                },
                "source": Path(c.source).name,
            }
            for c in checks
        ],
        "vocabulary": vocabulary_reference(hits_by_suspect=_hits_by_suspect(session)),
    }


# ── 误报记账：反向降噪 ──────────────────────────────────────────────────────


def report_false_positive(
    session: Session,
    *,
    suspect_type: str,
    fingerprint: str,
    job_id: str = "",
    page_no: int = 0,
    block_id: str = "",
    evidence: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """记一条「这不是问题」。

    只降权不屏蔽：同指纹下次仍然显示，但严重度降一档并标注疑似历史误报。没有这
    条反向通道，检测器只会越来越吵，永远拿不到收敛信号。
    """
    suspect = (suspect_type or "").strip().upper()
    if not suspect:
        raise ValueError("suspect_type required")

    job = session.get(CorpusImportJob, job_id) if job_id else None
    book_fp = ""
    if job is not None:
        book_fp = str((loads(job.meta_json, {}) or {}).get("fingerprint_hash") or "")

    pattern = (
        session.query(CorpusImprovementPattern)
        .filter(CorpusImprovementPattern.suspect_type == suspect)
        .first()
    )
    row = CorpusSymptomFalsePositive(
        suspect_type=suspect,
        fingerprint=(fingerprint or "")[:64],
        pattern_id=pattern.id if pattern is not None else "",
        job_id=job_id or "",
        fingerprint_hash=book_fp,
        page_no=int(page_no or 0),
        block_id=(block_id or "")[:64],
        evidence_json=dumps(evidence or {}),
        note=(note or "")[:2000],
        created_at=_now_iso(),
    )
    session.add(row)
    if pattern is not None:
        # hits 只由检测器命中累加（见 corpus_symptoms.enrich_findings），这里不动，
        # 否则误报率的分子分母会同时涨，永远看不出检测器在变吵。
        pattern.false_positive = int(pattern.false_positive or 0) + 1
        pattern.updated_at = _now_iso()
    session.flush()
    return {"ok": True, "id": row.id, "pattern_id": row.pattern_id}


def confirm_true_positive(
    session: Session, *, suspect_type: str, count: int = 1
) -> dict[str, Any]:
    """记一次「确实是问题且照手册修好了」。命中数由检测器记，这里只记判定。"""
    suspect = (suspect_type or "").strip().upper()
    pattern = (
        session.query(CorpusImprovementPattern)
        .filter(CorpusImprovementPattern.suspect_type == suspect)
        .first()
    )
    if pattern is None:
        raise ValueError(f"没有挂在症状 {suspect} 上的模式")
    pattern.true_positive = int(pattern.true_positive or 0) + max(1, int(count))
    pattern.updated_at = _now_iso()
    session.flush()
    return {"ok": True, "pattern_id": pattern.id, "true_positive": pattern.true_positive}


# ── 绑定检测器 ──────────────────────────────────────────────────────────────


def bind_detector(
    session: Session,
    *,
    pattern_id: str,
    suspect_type: str,
    detector_ref: str = "",
    remediation_kind: str = "",
    playbook_ref: str = "",
    landing_target: str = "",
) -> dict[str, Any]:
    """把一条模式和它的症状名、检测器、处置档位绑起来。

    ``detector_ref`` 必须指向 ``configs/checks/`` 里真实存在、且已加载成功的声明
    ——写一个查不出来的名字，模式看起来就像有检测器，实际永远不命中。
    """
    from app.services import improvement_points as ip_svc

    pattern = session.get(CorpusImprovementPattern, pattern_id)
    if pattern is None:
        raise ValueError(f"pattern not found: {pattern_id}")

    suspect = (suspect_type or "").strip().upper()
    if not suspect:
        raise ValueError("suspect_type required")

    kind = (remediation_kind or "").strip().lower()
    if kind and kind not in ("rule", "playbook"):
        raise ValueError("remediation_kind 只能是 rule 或 playbook")
    if kind == "playbook" and not (playbook_ref or pattern.playbook_ref):
        raise ValueError("playbook 档必须给出手册步骤")

    if detector_ref:
        known = {Path(c.source).name for c in load_checks()}
        if detector_ref not in known:
            raise ValueError(
                f"检测器声明未加载：{detector_ref}。已加载的有：{sorted(known) or '（无）'}"
            )
        by_type = {c.suspect_type for c in load_checks() if Path(c.source).name == detector_ref}
        if suspect not in by_type:
            raise ValueError(f"{detector_ref} 里的症状名不是 {suspect}")

    clash = (
        session.query(CorpusImprovementPattern)
        .filter(CorpusImprovementPattern.suspect_type == suspect)
        .filter(CorpusImprovementPattern.id != pattern_id)
        .first()
    )
    if clash is not None:
        raise ValueError(
            f"症状 {suspect} 已经绑在模式 {clash.id} 上；先 merge 再绑，别让一个症状对两个桶"
        )

    pattern.suspect_type = suspect
    if detector_ref:
        pattern.detector_ref = detector_ref
    if kind:
        pattern.remediation_kind = kind
    if playbook_ref:
        pattern.playbook_ref = playbook_ref[:8000]
    if landing_target:
        ip_svc.route_pattern(session, pattern_id, landing_target=landing_target)
    ip_svc.recount_pattern(session, pattern)
    session.flush()
    return {"ok": True, "pattern": ip_svc.get_pattern(session, pattern_id)}


# ── 晋级门 ──────────────────────────────────────────────────────────────────


def promotion_check(session: Session, pattern_id: str) -> dict[str, Any]:
    """playbook 能不能升成 rule。**只出提议，不写任何规则文件。**

    五道门缺一不可。自动写规则的诱惑很大，但规则一旦落进 layout_rules.yaml 就会
    默默改变全书渲染，出错时没人知道是谁改的。判断可以自动，落笔必须经人。
    """
    from app.services import improvement_points as ip_svc

    pattern = session.get(CorpusImprovementPattern, pattern_id)
    if pattern is None:
        raise ValueError(f"pattern not found: {pattern_id}")

    ip_svc.recount_pattern(session, pattern)
    session.flush()

    members = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.pattern_id == pattern_id)
        .all()
    )
    gates: list[dict[str, Any]] = []

    enough = int(pattern.member_count or 0) >= PROMOTE_MIN_MEMBERS
    cross_book = int(pattern.book_count or 0) >= PROMOTE_MIN_BOOKS
    gates.append(
        {
            "gate": "复现足够",
            "passed": enough and cross_book,
            "detail": (
                f"{pattern.member_count} 次复现、跨 {pattern.book_count} 本书"
                f"（要求 ≥{PROMOTE_MIN_MEMBERS} 次且 ≥{PROMOTE_MIN_BOOKS} 本）"
            ),
        }
    )

    tools = {
        str(step.get("tool") or "")
        for member in members
        for step in ((loads(member.procedure_json, {}) or {}).get("steps") or [])
    }
    tools.discard("")
    gates.append(
        {
            "gate": "修复动作一致",
            "passed": len(tools) == 1,
            "detail": f"成员用过的工具：{sorted(tools) or '（无记录）'}",
        }
    )

    formalizable = bool(pattern.detector_ref) and pattern.landing_target not in (
        "",
        "L3.book_patch",
    )
    gates.append(
        {
            "gate": "可形式化",
            "passed": formalizable,
            "detail": (
                f"检测器={pattern.detector_ref or '（无）'}，"
                f"生效面={pattern.landing_target or '（未定）'}"
            ),
        }
    )

    examples = {"passed": False, "detail": "没有检测器，无法回归"}
    if pattern.suspect_type:
        try:
            result = run_examples(suspect_type=pattern.suspect_type)
            examples = {
                "passed": bool(result["passed"]),
                "detail": (
                    f"召回 {result['recall']:.0%}、误报 {result['false_positive_rate']:.0%}"
                ),
            }
        except ValueError as exc:
            examples = {"passed": False, "detail": str(exc)}
    gates.append({"gate": "正反例全绿", **examples})

    hits = int(pattern.hits or 0)
    fp = int(pattern.false_positive or 0)
    fp_rate = (fp / hits) if hits else 0.0
    gates.append(
        {
            "gate": "误报库无冲突",
            "passed": fp == 0 or fp_rate <= MAX_FALSE_POSITIVE_RATE,
            "detail": (
                f"检测器命中 {hits} 处、人工判误报 {fp} 处"
                f"（上限 {MAX_FALSE_POSITIVE_RATE:.0%}）"
            ),
        }
    )

    passed = all(g["passed"] for g in gates)
    return {
        "ok": True,
        "pattern_id": pattern_id,
        "suspect_type": pattern.suspect_type or "",
        "current_kind": pattern.remediation_kind or "playbook",
        "promotable": passed,
        "gates": gates,
        "proposal": (
            f"建议把 {pattern.suspect_type} 从 playbook 升为 rule，"
            f"写入 {pattern.landing_target}。规则文件需人工批准后落笔。"
            if passed
            else "尚未够格晋级，先补齐未过的门。"
        ),
    }
