"""MinerU 全书导入会话状态：open_issues + effective_changes 账本。

payload 仍沿用 segment_* 字段名（旧会话断点兼容）；编排主路径为全书一次导入。
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

_SKIP_FULL = re.compile(
    r"(请把)?全文继续"
    r"|不用改|不用修|不修"
    r"|跳过(建议|修正|问题|这些)?"
    r"|先继续|别管(问题|这些)|dismiss"
    r"|全文导入|完成全文|继续全文"
    r"|继续完成全文|全文识别"
    r"|跳过.*继续|继续.*全文"
    r"|导入下一批|下一批|直接下一批",
    re.I,
)
_ACCEPT = re.compile(
    r"按你的建议|按建议改|接受建议|就这样改|可以改|确认有效|同意修|开始改|帮我改",
    re.I,
)
_CONFIRM_EFFECTIVE = re.compile(r"有效|可以了|没问题了|确认有效|就这样|看着对", re.I)
# 「修第 3、10 页」「只改 3 和 10」等：抽出页码
_PAGE_MENTION = re.compile(
    r"(?:第\s*)?(\d{1,4})\s*页|(?:^|[^\d])(\d{1,4})(?=\s*[、,，和与及/\s]|\s*$)",
    re.I,
)


def page_range_str(start: int, end: int) -> str:
    return f"{start}-{end}" if start != end else str(start)


def open_issues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("open_issues") or []
    return [x for x in raw if isinstance(x, dict)]


def open_issue_pages(payload: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    for it in open_issues(payload):
        if str(it.get("status") or "") != "open":
            continue
        try:
            pages.append(int(it["page"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(set(pages))


def has_blocking_open_issues(payload: dict[str, Any]) -> bool:
    return bool(open_issue_pages(payload))


def merge_open_issues(
    payload: dict[str, Any], new_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge by page+kind; new open overwrites same key if still open."""
    by_key: dict[str, dict[str, Any]] = {}
    for it in open_issues(payload):
        key = f"{it.get('page')}:{it.get('kind')}"
        by_key[key] = dict(it)
    for it in new_items:
        if not isinstance(it, dict):
            continue
        item = dict(it)
        item.setdefault("id", str(uuid.uuid4())[:8])
        item.setdefault("status", "open")
        key = f"{item.get('page')}:{item.get('kind')}"
        by_key[key] = item
    return list(by_key.values())


def mark_issues_status(
    payload: dict[str, Any],
    *,
    status: str,
    pages: list[int] | None = None,
) -> list[dict[str, Any]]:
    want = set(pages) if pages is not None else None
    out: list[dict[str, Any]] = []
    for it in open_issues(payload):
        item = dict(it)
        try:
            p = int(item.get("page"))
        except (TypeError, ValueError):
            out.append(item)
            continue
        if item.get("status") == "open" and (want is None or p in want):
            item["status"] = status
        out.append(item)
    return out


def imported_through(payload: dict[str, Any]) -> int:
    return max(0, int(payload.get("segment_imported_through") or 0))


def note_imported_through(payload: dict[str, Any], end: int) -> None:
    """记录已成功导入到的页码（含），避免重复启动 MinerU。"""
    payload["segment_imported_through"] = max(imported_through(payload), int(end))


def qa_through(payload: dict[str, Any]) -> int:
    """自动改/质检已完成到的页码（含）。导入完成 ≠ 自动改完成。"""
    return max(0, int(payload.get("segment_qa_through") or 0))


def note_qa_through(payload: dict[str, Any], end: int) -> None:
    """记录质检/自动改已完成到的页码。Agent 中途失败时不要调用。"""
    payload["segment_qa_through"] = max(qa_through(payload), int(end))


def gate_batch_end(payload: dict[str, Any]) -> int:
    """旧门闸字段读取（兼容历史 payload；新路径不再写入 segment_gate_end）。"""
    return max(0, int(payload.get("segment_gate_end") or 0))


def apply_qa_report_to_payload(
    payload: dict[str, Any], report: dict[str, Any], *, batch: str = ""
) -> dict[str, Any]:
    """把 Agent 质检/修正 JSON 同步进 open_issues（fixed_pages + 新 issues）。"""
    new_issues: list[dict[str, Any]] = []
    for it in report.get("issues") or []:
        if not isinstance(it, dict):
            continue
        try:
            p = int(it.get("page"))
        except (TypeError, ValueError):
            continue
        new_issues.append(
            {
                "page": p,
                "kind": str(it.get("kind") or "layout"),
                "status": "open",
                "batch": batch or str(it.get("batch") or ""),
                "summary": str(it.get("summary") or "")[:300],
                "suggested_action": str(it.get("suggested_action") or "")[:200],
            }
        )
    if new_issues:
        payload["open_issues"] = merge_open_issues(payload, new_issues)
    fixed_pages: list[int] = []
    for p in report.get("fixed_pages") or []:
        try:
            fixed_pages.append(int(p))
        except (TypeError, ValueError):
            continue
    if fixed_pages:
        payload["open_issues"] = mark_issues_status(
            payload, status="fixed", pages=fixed_pages
        )
    # 旧门闸 payload：needs_user=false 且 issues 空时，清掉 gate 范围内仍 open 的债
    if (
        report.get("needs_user") is False
        and not (report.get("issues") or [])
        and gate_batch_end(payload) > 0
    ):
        start = int(payload.get("segment_next_page") or 1)
        end = gate_batch_end(payload)
        in_batch = [p for p in open_issue_pages(payload) if start <= p <= end]
        if in_batch:
            payload["open_issues"] = mark_issues_status(
                payload, status="fixed", pages=in_batch
            )
    return payload


def user_wants_skip_and_continue(text: str) -> bool:
    t = (text or "").strip()
    return bool(t and _SKIP_FULL.search(t))


def user_accepts_suggestions(text: str) -> bool:
    t = (text or "").strip()
    return bool(t and _ACCEPT.search(t))


def user_confirms_effective(text: str) -> bool:
    t = (text or "").strip()
    return bool(t and _CONFIRM_EFFECTIVE.search(t))


def parse_page_ranges(spec: str | None) -> list[int]:
    """把 ``1-20`` / ``3,5,7-9`` 解析成页码列表。"""
    if not spec or not str(spec).strip():
        return []
    pages: list[int] = []
    for part in re.split(r"[,，\s]+", str(spec).strip()):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            pages.extend(range(lo, hi + 1))
        else:
            try:
                pages.append(int(part))
            except ValueError:
                continue
    return sorted(set(pages))


def import_batch_ready(
    job: dict[str, Any],
    *,
    page_ranges: str | None = None,
    page_limit: int | None = None,
) -> bool:
    """本批是否真的导完了（供 wait 循环用）。

    - 终态 ``parsed`` / ``chunked`` / ``indexed`` → 完成
    - 否则：``page_ranges``（或 ``page_limit`` 对应的 1..N）里每一页都在
      ``meta.pages_ready`` 里才算完成
    - **不再**把「pages_ready 非空」当成完成（曾导致首页一出就假完成）
    """
    status = str(job.get("status") or "")
    if status == "failed":
        return False
    if status in ("parsed", "chunked", "indexed"):
        return True
    meta = job.get("meta") or {}
    try:
        ready = {int(p) for p in (meta.get("pages_ready") or [])}
    except (TypeError, ValueError):
        ready = set()
    needed = parse_page_ranges(page_ranges)
    if not needed and page_limit:
        try:
            n = int(page_limit)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            needed = list(range(1, n + 1))
    if not needed:
        return False
    return all(p in ready for p in needed)


def mentioned_open_pages(text: str, open_pages: list[int]) -> list[int]:
    """用户话里**明确点到**的待办页（没点名则空列表）。"""
    open_set = set(open_pages)
    if not open_set:
        return []
    t = (text or "").strip()
    found: list[int] = []
    for m in _PAGE_MENTION.finditer(t):
        raw = m.group(1) or m.group(2)
        if not raw:
            continue
        try:
            p = int(raw)
        except ValueError:
            continue
        if p in open_set:
            found.append(p)
    return sorted(set(found))


def parse_user_fix_pages(text: str, open_pages: list[int]) -> list[int]:
    """从用户话里抽出要修的页；没点名则返回全部待办页。"""
    named = mentioned_open_pages(text, open_pages)
    return named if named else sorted(set(open_pages))


def append_effective_change(
    payload: dict[str, Any], change: dict[str, Any]
) -> list[dict[str, Any]]:
    items = list(payload.get("effective_changes") or [])
    item = dict(change)
    item.setdefault("id", str(uuid.uuid4())[:8])
    item.setdefault("confirmed_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    items.append(item)
    return items


def append_layout_change(
    payload: dict[str, Any], change: dict[str, Any]
) -> list[dict[str, Any]]:
    """全量排版改动账本（每次成功写 IR 必记，不依赖模型自觉）。

    每条 attempt 应带 tool / args_summary / block_ids，供 IP procedure 折叠。
    """
    items = list(payload.get("layout_change_log") or [])
    item = dict(change)
    item.setdefault("id", str(uuid.uuid4())[:8])
    item.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    item.setdefault("proposer", "ai")
    if not item.get("tool"):
        item["tool"] = str(item.get("op") or "unknown")
    if not item.get("args_summary"):
        item["args_summary"] = str(item.get("summary") or "")[:300]
    if item.get("block_ids") is None:
        item["block_ids"] = []
    # 截断过长 hint，保留可审计摘要
    for key in ("summary", "before_hint", "after_hint", "args_summary"):
        if key in item and item[key] is not None:
            item[key] = str(item[key])[:400]
    items.append(item)
    return items


def procedure_hints_instruction(payload: dict[str, Any], *, max_items: int = 12) -> str:
    """Inject accepted-IP procedures into autofix / focus prompts."""
    hints = payload.get("improvement_procedures") or []
    if not isinstance(hints, list) or not hints:
        return ""
    lines = [
        "## 已知成功路径（优先按此工具序列改；勿无未验证的替代打法）",
    ]
    for h in hints[:max_items]:
        if not isinstance(h, dict):
            continue
        page = h.get("page_no")
        summary = str(h.get("summary") or "")[:120]
        proc = h.get("procedure") or {}
        steps = proc.get("steps") if isinstance(proc, dict) else []
        step_s = " → ".join(
            str(s.get("tool") or "") for s in (steps or [])[:6] if isinstance(s, dict)
        )
        lines.append(f"- p{page} [{h.get('category')}] {summary}")
        if step_s:
            lines.append(f"  procedure: {step_s}")
    return "\n".join(lines) + "\n"


def layout_change_log(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("layout_change_log") or []
    return [x for x in raw if isinstance(x, dict)]


def has_layout_or_effective_ledger(payload: dict[str, Any]) -> bool:
    return bool(layout_change_log(payload) or payload.get("effective_changes"))


def fix_instruction(
    *,
    job_id: str,
    pages: list[int],
    edition_id: str = "",
    procedure_block: str = "",
) -> str:
    """手动指定页修改（事后优化 / 续聊）。仍以最终 HTML 验收。"""
    pages_s = ", ".join(str(p) for p in pages) if pages else "(无)"
    proc = f"\n{procedure_block}\n" if procedure_block else ""
    return (
        f"## 按用户指定修改 · job=`{job_id}` · 方案 `{edition_id or '?'}`\n"
        f"用户要求只修这些页：{pages_s}\n"
        f"{proc}"
        "1. 优先读 `layout_rule_id` / `layout_confidence`（`corpus.get_blocks`）；"
        "若有已知成功路径 → 优先按 procedure 工具序列；"
        "版式错 → page override 或 edition `layout_rules.yaml`，再 "
        "`corpus.rebuild_ir(job_id, pages_json=\"[…]\")`（恒为 layout-only）"
        "或 `rerender_page`。\n"
        "2. 单块错 → `patch_block` / `replace_page_blocks`；验收看最终 HTML。\n"
        "3. **禁止** 35B reprocess refine；禁止再 start_import。\n"
        "4. 确认有效须 `edition.record_effective_change`（accepted IP+procedure）。\n"
        "5. 结束后输出 ```json：`fixed_pages`、`issues`（遗留）、"
        "`needs_user`=false（除非真卡死）。\n"
    )


_EVIDENCE_VALUE_CHARS = 120
_EVIDENCE_MAX_FIELDS = 6


def _locate_evidence(finding: dict[str, Any]) -> dict[str, Any]:
    """按检测器的 `locate_by` 挑出定位用的证据子集。

    整个 `evidence` 不能给：一条命中动辄十几个字段，几十处症状乘上去就把上下文
    撑爆了。但全丢掉更糟——症状报了「这块该在侧栏」，不给 `zone` / `rel_x0`，
    照着改的人根本不知道改哪一块。`locate_by` 就是检测器自己声明的那一小撮
    「不给就没法动手」的字段（缺省是整个 evidence，见 RemediationSpec.locate_by）。
    """
    evidence = finding.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return {}
    fields = [str(f) for f in (finding.get("locate_by") or [])] or list(evidence)
    out: dict[str, Any] = {}
    for name in fields[:_EVIDENCE_MAX_FIELDS]:
        if name not in evidence:
            continue
        value = evidence[name]
        out[name] = value[:_EVIDENCE_VALUE_CHARS] if isinstance(value, str) else value
    return out


def _symptom_row(finding: dict[str, Any]) -> dict[str, Any]:
    """一条已知症状的执行视图：位置、定位证据、处置档位、上次的成功做法。"""
    row = {
        "suspect_type": finding.get("suspect_type") or "",
        "page_no": finding.get("page_no"),
        "block_id": finding.get("block_id") or "",
        "severity": finding.get("severity") or "",
        "match": finding.get("match") or "",
        "remediation_kind": finding.get("remediation_kind") or "",
        "landing_target": finding.get("landing_target") or "",
        "playbook_ref": finding.get("playbook_ref") or "",
        "candidates": finding.get("candidates") or [],
        "false_positive_hits": finding.get("false_positive_hits") or 0,
    }
    located = _locate_evidence(finding)
    if located:
        row["evidence"] = located
    return row


AUTOFIX_MAX_ROUNDS = 8
SYMPTOM_BATCH = 24


def remaining_actionable_findings(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """还有手册/落点、且没被判误报的命中。编排器用这个决定能不能收口。"""
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for finding in data.get("known") or []:
        if not isinstance(finding, dict):
            continue
        if int(finding.get("false_positive_hits") or 0) > 0:
            continue
        out.append(finding)
    return out


def batch_actionable_findings(
    findings: list[dict[str, Any]], *, limit: int = SYMPTOM_BATCH
) -> list[dict[str, Any]]:
    """同一症状名优先凑满一批，手册只做一次。"""
    if not findings:
        return []
    first = str(findings[0].get("suspect_type") or "")
    same = [
        f for f in findings if str(f.get("suspect_type") or "") == first
    ] or findings
    return same[: max(1, int(limit))]


def autofix_may_finish(data: dict[str, Any] | None) -> bool:
    """活清单里还有可改症状 → 不能把 fullbook_autofix_done 置真。"""
    return not remaining_actionable_findings(data)


def remaining_as_open_issues(
    findings: list[dict[str, Any]], *, batch: str = ""
) -> list[dict[str, Any]]:
    """把没消掉的可改症状落成会话遗留页，便于点「继续自动改」。"""
    items: list[dict[str, Any]] = []
    for finding in findings:
        try:
            page = int(finding.get("page_no"))
        except (TypeError, ValueError):
            continue
        kind = str(finding.get("suspect_type") or "layout")
        items.append(
            {
                "page": page,
                "kind": kind,
                "status": "open",
                "batch": batch,
                "summary": f"{kind} 仍在活清单",
            }
        )
    return items


def _playbook_block(findings: list[dict[str, Any]]) -> str:
    """按症状名去重的手册步骤。

    步骤是照着做的东西，给路径等于没给；但同一症状会命中几十处，逐条重复步骤又
    会把上下文撑爆，所以按 `suspect_type` 去重后单独列一份。
    """
    steps: dict[str, str] = {}
    for finding in findings:
        suspect = str(finding.get("suspect_type") or "")
        text = str(finding.get("playbook") or "").strip()
        if suspect and text and suspect not in steps:
            steps[suspect] = text[:800]
    if not steps:
        return ""
    lines = ["手册步骤（`playbook` 档照这个做，按症状名对号入座）："]
    for suspect, text in list(steps.items())[:12]:
        lines.append(f"- **{suspect}**\n{text}")
    return "\n".join(lines)


def symptom_hints_instruction(
    data: dict[str, Any] | None,
    *,
    batch: list[dict[str, Any]] | None = None,
    round_no: int = 1,
    remaining_n: int | None = None,
) -> str:
    """把 `/jobs/{id}/symptoms` 的返回摊成指令块。

    只给本批 ``batch``，但标题写清全书还剩多少。禁止静默丢掉后半本。
    """
    import json

    if not isinstance(data, dict):
        return ""
    known = [x for x in (data.get("known") or []) if isinstance(x, dict)]
    unknown = [x for x in (data.get("unknown") or []) if isinstance(x, dict)]
    actionable = remaining_actionable_findings(data)
    work = batch if batch is not None else batch_actionable_findings(actionable)
    if not known and not unknown:
        return ""
    left = remaining_n if remaining_n is not None else len(actionable)
    out = [
        f"### 命名症状 · 第 {round_no} 轮"
        f"（有手册可改还剩 {left} 处；未知 {len(unknown)} 处）",
        "编排器会在你交报告后**重跑检测**。活清单里还有手册可改的，"
        "不算完，还会再给你下一批。交 JSON 报告本身不能收口。",
    ]
    if work:
        out.append(
            "本批先改这些（`rule` 档按 `landing_target` 落规则，`playbook` 档照下方"
            "「手册步骤」做；不是问题就 `checks.report_false_positive`）：\n"
            "```json\n"
            + json.dumps(
                [_symptom_row(f) for f in work], ensure_ascii=False
            )[:8000]
            + "\n```"
        )
        book = _playbook_block(work)
        if book:
            out.append(book)
    elif unknown:
        out.append(
            "本批没有待改的有手册症状。未知疑点查清后要么修、要么 "
            "`checks.report_false_positive`，不要只写进报告就停：\n"
            "```json\n"
            + json.dumps(
                [_symptom_row(f) for f in unknown[:12]], ensure_ascii=False
            )[:3000]
            + "\n```"
        )
    return "\n\n".join(out)


def fullbook_analyze_and_fix_instruction(
    *,
    job_id: str,
    page_count: int,
    edition_id: str,
    escalate_pages: list[int],
    escalate_summary: dict[str, Any] | None = None,
    open_pages: list[int] | None = None,
    procedure_block: str = "",
    symptom_block: str = "",
) -> str:
    """全书导入完成后：消费 escalate 信号 → 看最终 HTML → 自动改（无用户门闸）。"""
    esc = sorted({int(p) for p in escalate_pages})
    esc_head = ", ".join(str(p) for p in esc[:40])
    if len(esc) > 40:
        esc_head += f" …共 {len(esc)} 页"
    summary = escalate_summary or {}
    summary_hint = ""
    if summary:
        # Keep instruction bounded.
        import json

        slim = {k: summary[k] for k in list(summary.keys())[:24]}
        summary_hint = (
            "\n`layout_escalate_summary`（节选）：\n```json\n"
            + json.dumps(slim, ensure_ascii=False)[:2500]
            + "\n```\n"
        )
    open_hint = ""
    if open_pages:
        open_hint = f"会话内仍有待办页：{open_pages}（一并处理）。\n"
    proc_hint = f"\n{procedure_block}\n" if procedure_block else ""
    sym_hint = f"\n{symptom_block}\n" if symptom_block else ""

    return (
        f"## MinerU 全书导入完成 · Cursor 自动分析与修改\n"
        f"- job_id=`{job_id}` · 全书 {page_count} 页 · 方案 `{edition_id}`\n"
        f"- 导入 worker 已跑完 **几何+规则**（A 层）；最终 HTML 已在中栏。\n"
        f"- 低置信页清单 `layout_escalate_pages`（成本阶梯第二级 → Cursor）："
        f"{esc_head or '（无）'}\n"
        f"{summary_hint}{open_hint}{proc_hint}{sym_hint}\n"
        "### 本轮职责（分析 + 直接改完，不要等用户确认）\n"
        "0. **先修已知症状**（若上文有清单）：这些是别的书上已经修好过的问题，"
        "`rule` 档按 `landing_target` 落规则，`playbook` 档照步骤做，"
        "`candidates` 里的「历史修正」直接照抄。判定它其实不是问题就调 "
        "`checks.report_false_positive`，别硬改。\n"
        "1. **再**处理 escalate 页：`corpus.page_html` 看最终 HTML；"
        "`corpus.get_blocks` 读 `layout_zone` / `layout_rule_id` / "
        "`layout_confidence_score` / `layout_evidence`，对照 summary.low_keys。\n"
        "2. 再抽查其它明显坏页（乱栏、公式崩、明显缺漏）。\n"
        "3. 修复通道（便宜优先，见 page-diagnosis / 40-layout-rules-first）：\n"
        "   - 若上文列出「已知成功路径」→ **优先按该 procedure 的工具序列**改；"
        "偏离且验收失败时不得覆盖旧 accepted。\n"
        "   - 版式归属系统性错 → 写 `configs/editions/<edition>/layout_rules.yaml` "
        "或 `overrides/pages/NNNN.yaml`（四位页号，经 fs.request_edit 批准），再 "
        "`corpus.rebuild_ir(job_id, pages_json=\"[12,34]\")` 重跑"
        "（恒为 layout-only；留空＝全书）；\n"
        "   - 单块错 → `patch_block` / `replace_page_blocks`，必要时 `rerender_page`。\n"
        "4. **禁止** 35B：不得 `layout.judge_35b` / triad / compare；"
        "不得 `reprocess_page` 的 content_correct / layout_content / layout_rebuild / "
        "refine_35b；不得再 `start_import`。\n"
        "5. **不要**停下来问「按建议改吗」——直接改本批并验收最终 HTML。"
        "确认有效须 `edition.record_effective_change`（会写入 IP+procedure）。\n"
        "6. 有有效改动 → `edition.record_effective_change`。"
        "本轮只改上面这一批；交报告后编排器会重跑检测，剩的再开一轮。"
        "**不要**因为写了 JSON 就声称全书已改完。\n"
        "\n本轮结束输出 ```json：\n"
        "```json\n"
        "{\n"
        '  "job_id": "' + job_id + '",\n'
        '  "import_line": "mineru",\n'
        '  "escalate_handled": [],\n'
        '  "fixed_pages": [],\n'
        '  "issues": [],\n'
        '  "needs_user": false,\n'
        '  "result_edition_id": "",\n'
        '  "notes": "…"\n'
        "}\n"
        "```\n"
        "- `needs_user` 仅在真正无法继续时为 true；默认 false。\n"
        "- 用中文向用户简要说明改了什么；事后用户可再逐页优化。\n"
    )


def extract_qa_report(text: str) -> dict[str, Any] | None:
    import json
    from re import S, search

    m = search(r"```json\s*(\{.*?\})\s*```", text or "", S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data
