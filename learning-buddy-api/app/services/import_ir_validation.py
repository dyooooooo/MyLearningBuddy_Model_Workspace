"""T1 deterministic checks for MinerU/corpus DocumentBlock pages.

Isolated from ``import_35b.validation`` — same idea (findings as suspects),
different IR (DocumentBlock + HTML, not PageBlueprint).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.document_ir import DocumentBlock

Severity = Literal["high", "medium", "low"]

_FALLBACK_SPAN_RE = re.compile(r'<span class="math math--fallback">([^<]*)</span>')
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Finding(BaseModel):
    suspect_type: str
    severity: Severity = "medium"
    page_no: int
    block_id: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    candidates: list[str] = Field(default_factory=list)
    fingerprint: str = ""
    fingerprint_exact: str = ""
    detector: str = "rule"

    def finalize(self) -> "Finding":
        core = f"{self.suspect_type}|{self.block_id}|{_norm_evidence(self.evidence)}"
        self.fingerprint = hashlib.sha1(core.encode("utf-8")).hexdigest()[:16]
        self.fingerprint_exact = hashlib.sha1(
            f"{core}|p{self.page_no}".encode("utf-8")
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
) -> list[Finding]:
    """Return T1 findings for one corpus page."""
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
