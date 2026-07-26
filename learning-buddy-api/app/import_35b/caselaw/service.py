"""判例库业务：裁决落判例（record_case）+ finding 富化（enrich_findings）。

设计边界（对齐架构文档 4.2）：
- **record_case**：人每次裁决都沉淀为判例。canonical 行始终写 Postgres（纯审计
  数据，零行为变更，用于积累到激活门槛 + 供离线归纳）；向量索引仅在
  ``caselaw_enabled`` 时写独立 Milvus collection。永不抛异常阻断裁决主流程。
- **enrich_findings**：仅在 ``caselaw_enabled`` 且判例数 ≥ ``caselaw_min_cases``
  时生效；否则原样返回。判例只做两件事——**降权**（命中误判库 → severity 降一档
  + 补「疑似历史误报」候选）与**猜选项**（命中方案库 → 补历史修正候选）。
  **永不静默抑制**：抑制只能由人经 ``import35b_suppressions`` 显式完成。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.import_35b import store
from app.import_35b.caselaw import store as vec_store
from app.util.json_util import loads

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = ["high", "medium", "low"]

# 裁决 → 判例库类型
_MISJUDGMENT = "misjudgment"
_SOLUTION = "solution"


def _downgrade(severity: str) -> str:
    try:
        idx = _SEVERITY_ORDER.index(severity)
    except ValueError:
        return severity
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


def finding_case_text(suspect_type: str, evidence: dict[str, Any]) -> str:
    """判例可检索文本：suspect_type + evidence 值摘要（embedding 输入，稳定可复现）。"""
    parts = [suspect_type]
    for k in sorted(evidence or {}):
        v = str(evidence[k])[:200]
        if v:
            parts.append(f"{k}: {v}")
    return " | ".join(parts)


# ── 记录：裁决 → 判例 ───────────────────────────────────────────────────────


def record_case(
    session: Session,
    *,
    item: Any,
    subject: str,
    grade_band: str,
    decision: dict[str, Any],
) -> str | None:
    """把一条裁决沉淀为判例。``item`` 是 Import35bReviewItem。返回 case_id 或 None。

    误报裁决 → 误判库；修正裁决 → 方案库；其他裁决（confirm）不记（无监督信号）。
    绝不抛异常——判例记录失败不得影响裁决本身。
    """
    action = str(decision.get("action") or "")
    if action == "false_positive":
        kind = _MISJUDGMENT
    elif action == "correct":
        kind = _SOLUTION
    else:
        return None

    try:
        evidence = loads(getattr(item, "evidence_json", "") or "{}", {})
        case_text = finding_case_text(item.suspect_type, evidence)
        row = store.add_caselaw(
            session,
            kind=kind,
            suspect_type=item.suspect_type,
            subject=subject,
            grade_band=grade_band,
            fingerprint=item.fingerprint or "",
            case_text=case_text,
            decision=decision,
            review_item_id=item.id,
            job_id=item.job_id,
            page_no=item.page_no,
        )
    except Exception:  # noqa: BLE001
        logger.exception("caselaw: record_case canonical write failed")
        return None

    # 向量索引（best-effort，仅在开关开启时触碰 Milvus；隔离于语料库 collection）
    if settings.import35b_caselaw_enabled:
        try:
            ok = vec_store.index_case(
                case_id=row.id,
                kind=kind,
                suspect_type=item.suspect_type,
                subject=subject or "math",
                grade_band=grade_band or "hs",
                fingerprint=item.fingerprint or "",
                text=case_text,
            )
            store.set_caselaw_vector_status(session, row.id, "indexed" if ok else "skip")
        except Exception:  # noqa: BLE001
            logger.warning("caselaw: vector index failed case=%s", row.id)
    return row.id


# ── 富化：判例 → finding 降权/猜选项 ────────────────────────────────────────


def _enrich_active(session: Session) -> bool:
    if not settings.import35b_caselaw_enabled:
        return False
    try:
        total = store.count_caselaw(session)
    except Exception:  # noqa: BLE001
        return False
    return total >= settings.import35b_caselaw_min_cases


def enrich_findings(
    session: Session,
    findings: list[dict[str, Any]],
    *,
    subject: str,
    grade_band: str,
) -> list[dict[str, Any]]:
    """用判例库对 finding 做非破坏性富化：降权 + 补候选。默认/未激活时原样返回。"""
    if not findings or not _enrich_active(session):
        return findings
    if not vec_store.available():
        return findings

    subj = subject or "math"
    gb = grade_band or "hs"
    fp_thr = settings.import35b_caselaw_fp_threshold
    hint_thr = settings.import35b_caselaw_hint_threshold

    for f in findings:
        # 用户主动报告的 finding 不参与判例富化（那是人明确的诉求，不是疑点）
        if str(f.get("detector") or "rule") == "user_report":
            continue
        text = finding_case_text(
            str(f.get("suspect_type") or ""), f.get("evidence") or {}
        )
        if not text.strip():
            continue

        try:
            mis = vec_store.search_cases(text, kind=_MISJUDGMENT, subject=subj, grade_band=gb)
            sol = vec_store.search_cases(text, kind=_SOLUTION, subject=subj, grade_band=gb)
        except Exception:  # noqa: BLE001
            continue

        evidence = dict(f.get("evidence") or {})
        candidates = list(f.get("candidates") or [])
        hits_meta: list[dict[str, Any]] = []

        top_mis = mis[0] if mis else None
        if top_mis and top_mis.score >= fp_thr:
            # 降权一档 + 补候选；绝不静默抑制
            f["severity"] = _downgrade(str(f.get("severity") or "medium"))
            hint = f"疑似历史误报（相似度 {top_mis.score:.2f}）"
            if hint not in candidates:
                candidates.append(hint)
            hits_meta.append(
                {"kind": _MISJUDGMENT, "score": round(top_mis.score, 4), "case_id": top_mis.case_id}
            )

        for hit in sol:
            if hit.score < hint_thr:
                continue
            case = session.get(_caselaw_model(), hit.case_id)
            if case is None:
                continue
            dec = loads(case.decision_json, {})
            new_val = str(dec.get("new") or "").strip()
            note = str(dec.get("note") or "").strip()
            suggestion = new_val or note
            if suggestion:
                cand = f"历史修正：{suggestion[:80]}（相似度 {hit.score:.2f}）"
                if cand not in candidates:
                    candidates.append(cand)
                hits_meta.append(
                    {"kind": _SOLUTION, "score": round(hit.score, 4), "case_id": hit.case_id}
                )
            break  # 只取最相似的一条方案，避免候选爆炸

        if hits_meta:
            evidence["caselaw_hits"] = hits_meta
            f["evidence"] = evidence
            f["candidates"] = candidates

    return findings


def _caselaw_model():
    from app.db.models import Import35bCaseLaw

    return Import35bCaseLaw
