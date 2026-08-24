"""T1 deterministic checks for MinerU/corpus DocumentBlock pages.

Isolated from ``import_35b.validation`` — same idea (findings as suspects),
different IR (DocumentBlock + HTML, not PageBlueprint).
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.document_ir import DocumentBlock

logger = logging.getLogger(__name__)

Severity = Literal["high", "medium", "low"]

_FALLBACK_SPAN_RE = re.compile(r'<span class="math math--fallback">([^<]*)</span>')
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Finding(BaseModel):
    suspect_type: str
    severity: Severity = "medium"
    page_no: int
    block_id: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    # evidence 里「不给就没法动手」的那几个字段名，来自检测器的 remediation.locate_by。
    # 改进器只拿这个子集，所以要跟着这条命中一路传到入库。
    locate_by: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    fingerprint: str = ""
    fingerprint_exact: str = ""
    detector: str = "rule"
    # 映射结果，由 corpus_symptoms.enrich_findings 填。空 = 这是个新症状。
    pattern_id: str = ""
    match: str = ""  # exact | suspect_type
    remediation_kind: str = ""
    landing_target: str = ""
    playbook_ref: str = ""  # 指针：configs/checks/x.yaml#remediation.playbook
    playbook: str = ""  # 按指针解析出来的手册步骤全文

    def finalize(self) -> "Finding":
        # 指纹**不含块 ID 和页码**：块 ID 换本书就没有意义，而这个指纹的全部用途
        # 就是跨书认出同一个症状。要定位到具体一处用 fingerprint_exact。
        core = f"{self.suspect_type}|{_norm_evidence(self.evidence)}"
        self.fingerprint = hashlib.sha1(core.encode("utf-8")).hexdigest()[:16]
        self.fingerprint_exact = hashlib.sha1(
            f"{core}|p{self.page_no}|{self.block_id}".encode("utf-8")
        ).hexdigest()[:16]
        return self


def _norm_evidence(evidence: dict[str, Any]) -> str:
    parts = [f"{k}={str(v)[:120]}" for k, v in sorted(evidence.items())]
    return ";".join(parts)


def run_page_checks(
    blocks: list[DocumentBlock],
    html: str,
    *,
    page_no: int,
    with_declared: bool = True,
) -> list[Finding]:
    """Return T1 findings for one corpus page.

    两个来源：这里硬编码的四条基础检查（页空、主栏空、公式回退、控制字符），
    加上 ``configs/checks/`` 里 Agent 抽象出来的声明式检测器。前者是不变量，
    后者随模式库增长。
    """
    findings = _builtin_checks(blocks, html, page_no=page_no)
    if with_declared:
        findings.extend(_declared_checks(blocks, html, page_no=page_no))
    return findings


def _declared_checks(
    blocks: list[DocumentBlock], html: str, *, page_no: int
) -> list[Finding]:
    # 局部 import：corpus_symptoms 要读 DB 模型，而这个模块被纯渲染路径引用。
    from app.services.corpus_symptoms import run_checks_on_page

    try:
        hits = run_checks_on_page(blocks, html, page_no=page_no)
    except Exception as exc:  # noqa: BLE001 — 声明式检测器绝不阻断导入
        logger.warning("declared symptom checks failed on page %s: %s", page_no, exc)
        return []
    return [
        Finding(
            suspect_type=hit.suspect_type,
            severity=hit.severity,
            page_no=hit.page_no,
            block_id=hit.block_id,
            evidence=hit.evidence,
            locate_by=list(hit.locate_by),
            detector=hit.detector or "declared",
        ).finalize()
        for hit in hits
    ]


def _builtin_checks(
    blocks: list[DocumentBlock],
    html: str,
    *,
    page_no: int,
) -> list[Finding]:
    findings: list[Finding] = []
    page_blocks = [b for b in blocks if b.page_no == page_no]
    blank = any(bool((b.meta or {}).get("blank")) for b in page_blocks)

    if blank:
        return findings

    if not page_blocks:
        findings.append(
            Finding(
                suspect_type="PAGE_EMPTY",
                severity="high",
                page_no=page_no,
                evidence={"block_count": 0},
                candidates=["空白页", "MinerU 漏检", "页码范围截断"],
            ).finalize()
        )
        return findings

    text_len = sum(b.content_length() for b in page_blocks)
    main_match = re.search(
        r'<section class="lb-main"[^>]*>(.*?)</section>', html or "", re.S
    )
    main_inner = (main_match.group(1) if main_match else "") or ""
    main_text = re.sub(r"<[^>]+>", "", main_inner).strip()

    if text_len == 0 or not main_text:
        findings.append(
            Finding(
                suspect_type="MAIN_EMPTY",
                severity="high",
                page_no=page_no,
                evidence={"text_len": text_len, "main_chars": len(main_text)},
                candidates=["主栏空白", "块被 skip_render", "zone 错位"],
            ).finalize()
        )

    for m in _FALLBACK_SPAN_RE.finditer(html or ""):
        findings.append(
            Finding(
                suspect_type="MATH_FALLBACK",
                severity="medium",
                page_no=page_no,
                evidence={"snippet": m.group(1)[:80]},
                candidates=["公式未渲染", "LaTeX 损坏"],
            ).finalize()
        )

    for b in page_blocks:
        raw = (b.text or "") + (b.table_html or "") + (b.caption or "")
        if _CTRL_RE.search(raw):
            findings.append(
                Finding(
                    suspect_type="CONTROL_CHAR_RESIDUE",
                    severity="low",
                    page_no=page_no,
                    block_id=b.id,
                    evidence={"preview": raw[:60]},
                    candidates=["OCR 脏字符", "编码问题"],
                ).finalize()
            )

    return findings
