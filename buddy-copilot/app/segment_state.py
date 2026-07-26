"""MinerU 分段导入会话状态：open_issues 门闸 + effective_changes 账本。"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

IMPORT_PAGES_PER_SEGMENT = 20

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


def segment_batches(
    page_count: int, batch_size: int = IMPORT_PAGES_PER_SEGMENT
) -> list[tuple[int, int]]:
    n = max(0, int(page_count))
    if n <= 0:
        return []
    out: list[tuple[int, int]] = []
    start = 1
    while start <= n:
        end = min(start + batch_size - 1, n)
        out.append((start, end))
        start = end + 1
    return out


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
    """记录已成功导入到的页码（含），避免同批重复 MinerU。"""
    payload["segment_imported_through"] = max(imported_through(payload), int(end))


def qa_through(payload: dict[str, Any]) -> int:
    """质检已完成到的页码（含）。导入完成 ≠ 质检完成。"""
    return max(0, int(payload.get("segment_qa_through") or 0))


def note_qa_through(payload: dict[str, Any], end: int) -> None:
    """记录质检已完成到的页码。Agent 中途失败时不要调用。"""
    payload["segment_qa_through"] = max(qa_through(payload), int(end))


def gate_batch_end(payload: dict[str, Any]) -> int:
    return max(0, int(payload.get("segment_gate_end") or 0))


def set_gate_batch(payload: dict[str, Any], start: int, end: int) -> None:
    """质检门闸：本批页已导入，断点停在批首；跳过/通过后应 advance。"""
    payload["segment_next_page"] = int(start)
    payload["segment_gate_end"] = int(end)
    note_imported_through(payload, end)


def advance_past_gate_batch(payload: dict[str, Any], page_count: int = 0) -> int:
    """跳过/通过门闸后前进到下一批起点。返回新的 segment_next_page。"""
    end = gate_batch_end(payload)
    nxt = int(payload.get("segment_next_page") or 1)
    if end > 0:
        nxt = end + 1
    else:
        pc = int(page_count or payload.get("page_count") or 0)
        for a, b in segment_batches(pc):
            if a <= nxt <= b:
                nxt = b + 1
                break
        else:
            nxt = nxt + IMPORT_PAGES_PER_SEGMENT
    payload["segment_next_page"] = nxt
    payload.pop("segment_gate_end", None)
    return nxt


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
    # Agent 明确 needs_user=false 且 issues 空 → 清掉当前门闸批上仍 open 的债
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
    """全量排版改动账本（每次成功写 IR 必记，不依赖模型自觉）。"""
    items = list(payload.get("layout_change_log") or [])
    item = dict(change)
    item.setdefault("id", str(uuid.uuid4())[:8])
    item.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    item.setdefault("proposer", "ai")
    # 截断过长 hint，保留可审计摘要
    for key in ("summary", "before_hint", "after_hint"):
        if key in item and item[key] is not None:
            item[key] = str(item[key])[:400]
    items.append(item)
    return items


def layout_change_log(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("layout_change_log") or []
    return [x for x in raw if isinstance(x, dict)]


def has_layout_or_effective_ledger(payload: dict[str, Any]) -> bool:
    return bool(layout_change_log(payload) or payload.get("effective_changes"))


def qa_instruction(
    *,
    job_id: str,
    start: int,
    end: int,
    page_count: int,
    run_index: int,
    run_total: int,
    open_pages: list[int],
    edition_id: str,
) -> str:
    """质检轮：只看最终 HTML / 渲染，只列问题，默认不改。"""
    open_hint = (
        f"上一批仍未关闭的问题页（一并写进清单即可，本轮仍不要改）：{open_pages}\n"
        if open_pages
        else ""
    )
    return (
        f"## MinerU 分段质检 · 第 {run_index}/{run_total} 批（页 {start}–{end}）\n"
        f"- job_id=`{job_id}` · 全书 {page_count} 页 · 方案 `{edition_id}`\n"
        f"- 本批已由 MinerU 识别并生成最终 HTML；中栏可预览。\n"
        f"{open_hint}\n"
        "### 本轮职责（只报告，不修改）\n"
        "1. 用 `corpus.page_html` 查看本批各页的**最终 HTML**；"
        "拿不准时用 `corpus.render_preview` + 文件 Read 看**渲染效果**。\n"
        "2. 依据最终页面判断有没有问题（排版乱、公式/表格渲染错、明显缺漏等）。\n"
        "3. **有问题 → 只写入 issues 清单**（哪一页、什么现象、建议怎么改）；"
        "然后停下来等用户决定修不修、修哪几页。\n"
        "4. **本轮禁止改页**：不得调用 `corpus.get_blocks` / `patch_block` / "
        "`replace_page_blocks` / `rerender_page` / `reprocess_page`；"
        "不得把 MinerU 中间结构化块 / JSON 当质检依据"
        "（那是间接材料，最终以 HTML / 渲染为准）。\n"
        "5. **禁止** 35B：不得 layout.judge_35b / triad / compare，"
        "也不得对 reprocess 使用 content_correct / layout_content / layout_rebuild。\n"
        "6. 禁止对本批以外未列出的页做导入；禁止 start_import 新 job。\n"
        "\n本轮结束输出 ```json 块（非最终全书报告）：\n"
        "```json\n"
        "{\n"
        f'  "pages_done": "{start}-{end}",\n'
        '  "issues": [\n'
        '    {"page": 1, "kind": "layout|formula|table", "summary": "页面上看到的现象…", '
        '"suggested_action": "一句话建议"}\n'
        "  ],\n"
        '  "fixed_pages": [],\n'
        '  "needs_user": true,\n'
        '  "notes": "…"\n'
        "}\n"
        "```\n"
        "- 无问题：`issues` 为空、`needs_user`=false、`fixed_pages` 保持空。\n"
        "- 有问题：`needs_user`=true，`fixed_pages` 必须为空（本轮未改任何页）。\n"
        "- 用中文向用户简要说明问题清单，并提示可选「不修，下一批」或指定要修的页。\n"
    )


def fix_instruction(
    *,
    job_id: str,
    pages: list[int],
    edition_id: str = "",
) -> str:
    """用户确认后的修改轮：只改指定页，仍以最终 HTML / 渲染验收。"""
    pages_s = ", ".join(str(p) for p in pages) if pages else "(无)"
    return (
        f"## 按用户指定修改 · job=`{job_id}` · 方案 `{edition_id or '?'}`\n"
        f"用户要求只修这些页：{pages_s}\n"
        "1. 对这些页用 `corpus.get_blocks` → `replace_page_blocks` / `patch_block` 修改；"
        "需要时 `rerender_page`。\n"
        "2. 验收只看最终 HTML / 渲染：`corpus.page_html`，必要时 "
        "`render_preview`+Read；看返回的 `html_changed` / `rendered_snippet`，"
        "已生效就不要再空转重渲。\n"
        "3. **禁止** 35B reprocess refine；禁止 start_import / extend；"
        "禁止改用户未点名的页。\n"
        "4. 确认有效可用 `edition.record_effective_change`。\n"
        "5. 结束后输出 ```json：`fixed_pages`（已修好）、"
        "`issues`（仍无解的，needs_user=true）、无遗留则 needs_user=false。\n"
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
