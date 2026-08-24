"""Trace upstream cause for fs_guard / pipeline events.

Guard hits are effects. The cause is always earlier in the session:
user request → AI plan/tools → (optional) unauthorized file edits → rollback.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_SKIP_USER = re.compile(
    r"^(按建议改|继续|不修|下一批|好的|ok|OK|是|否|同意|取消)[.。!！]?$"
)
_FOCUS_PAGE = re.compile(r"page_no\s*=\s*(\d+)", re.I)
_FOCUS_BLOCK = re.compile(r"block_id\s*=\s*([^\s\n]+)", re.I)
_FOCUS_JOB = re.compile(r"job_id\s*=\s*([^\s\n]+)", re.I)
_WRITEISH = (
    "write",
    "edit",
    "patch",
    "shell",
    "bash",
    "apply",
    "fs.",
    "file",
    "run_",
    "terminal",
)


def _text_of(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or content.get("report") or "").strip()
    if isinstance(content, str):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                return str(obj.get("text") or obj.get("report") or content).strip()
        except json.JSONDecodeError:
            pass
        return content.strip()
    return str(content or "").strip()


def _is_substantive_user(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2:
        return False
    if _SKIP_USER.match(t):
        return False
    return True


def extract_focus_meta(*texts: str) -> dict[str, Any]:
    """Parse page/block/job from Studio「聚焦目标」 user blobs (latest wins)."""
    page_no = 0
    block_id = ""
    job_id = ""
    focus_text = ""
    for text in reversed([str(t or "") for t in texts if str(t or "").strip()]):
        if "page_no=" not in text and "【聚焦目标】" not in text:
            continue
        pm = _FOCUS_PAGE.search(text)
        if not pm:
            continue
        page_no = int(pm.group(1))
        bm = _FOCUS_BLOCK.search(text)
        raw_b = (bm.group(1) if bm else "").strip()
        block_id = "" if raw_b in ("", "None", "null", "undefined") else raw_b
        jm = _FOCUS_JOB.search(text)
        job_id = (jm.group(1) if jm else "").strip()
        focus_text = text
        break
    return {
        "page_no": page_no,
        "block_id": block_id,
        "job_id": job_id,
        "focus_text": focus_text,
    }


def dialogue_slot(page_no: int, user_input: str, guard_seq: int = 0) -> str:
    """One user dialogue → one locus slot (stable short hash)."""
    raw = f"{int(page_no or 0)}\n{(user_input or '').strip()[:240]}\n{int(guard_seq or 0)}"
    return "dlg:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _with_focus_meta(chain: dict[str, Any], extra_texts: list[str] | None = None) -> dict[str, Any]:
    texts = list(chain.get("user_requests") or [])
    if chain.get("user_input"):
        texts.append(str(chain["user_input"]))
    if extra_texts:
        texts.extend(extra_texts)
    meta = extract_focus_meta(*texts)
    chain["page_no"] = int(meta.get("page_no") or 0)
    chain["block_id"] = str(meta.get("block_id") or "")
    chain["job_id"] = str(meta.get("job_id") or "")
    chain["dialogue_slot"] = dialogue_slot(
        int(chain["page_no"] or 0),
        str(chain.get("user_input") or ""),
        int(chain.get("guard_seq") or 0),
    )
    # If last user line lacks page, keep short ask but annotate
    ui = str(chain.get("user_input") or "").strip()
    if chain["page_no"] and ui and "page_no=" not in ui and "【页" not in ui[:8]:
        chain["user_input_display"] = f"【页 {chain['page_no']}】\n{ui}"
    else:
        chain["user_input_display"] = ui
    return chain


def _is_writeish_tool(tool: str) -> bool:
    t = (tool or "").lower()
    return any(x in t for x in _WRITEISH)


def find_guard_seq(
    messages: list[dict[str, Any]],
    *,
    paths: list[str] | None = None,
    detail: str = "",
    near_seq: int | None = None,
) -> int | None:
    """Return seq of the system message that reports this guard hit."""
    full_paths = [p for p in (paths or []) if p][:12]
    basenames = [p.split("/")[-1] for p in full_paths if p]
    detail_bits = [
        ln.strip("- ").strip()
        for ln in (detail or "").splitlines()
        if ln.strip() and "fs_guard" not in ln and "已回滚" not in ln
    ][:6]
    best: tuple[int, int, int] | None = None  # (score, -distance, seq)
    for m in messages:
        role = str(m.get("role") or "")
        if role != "system":
            continue
        body = _text_of(m.get("content"))
        if "fs_guard" not in body and "越权文件" not in body:
            continue
        score = 0
        for p in full_paths:
            if p and p in body:
                score += 8
        for n in basenames:
            if n and n in body:
                score += 2
        for b in detail_bits:
            if len(b) >= 12 and b[:60] in body:
                score += 4
        if score <= 0:
            continue
        seq = int(m.get("seq") or 0)
        dist = abs(seq - near_seq) if near_seq else 0
        # Prefer higher path score; then nearer to near_seq; then later seq
        cand = (score, -(dist if near_seq else 10**9), seq)
        if best is None or cand > best:
            best = cand
    return best[2] if best else None


def trace_before_guard(
    messages: list[dict[str, Any]],
    guard_seq: int,
    *,
    lookback: int = 120,
    max_user: int = 3,
    max_assist: int = 2,
    max_tools: int = 12,
) -> dict[str, Any]:
    """Build causal chain for messages with seq < guard_seq."""
    window = [
        m
        for m in messages
        if int(m.get("seq") or 0) < guard_seq
        and int(m.get("seq") or 0) >= max(0, guard_seq - lookback)
    ]
    users: list[str] = []
    for m in reversed(window):
        if str(m.get("role") or "") != "user":
            continue
        text = _text_of(m.get("content"))
        if not _is_substantive_user(text):
            continue
        users.append(text)  # full user message, no truncation
        if len(users) >= max_user:
            break
    users.reverse()

    assists: list[str] = []
    for m in reversed(window):
        if str(m.get("role") or "") != "assistant":
            continue
        text = _text_of(m.get("content"))
        if len(text) < 20:
            continue
        # Skip JSON / machine dumps — want human-readable plan/result
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("```json"):
            continue
        if '"status"' in text[:80] and '"job_id"' in text[:400]:
            continue
        assists.append(text[:1500])
        if len(assists) >= max_assist:
            break
    assists.reverse()

    tools: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in window:
        if str(m.get("role") or "") != "step":
            continue
        content = m.get("content")
        if not isinstance(content, dict):
            try:
                content = json.loads(content) if isinstance(content, str) else {}
            except json.JSONDecodeError:
                content = {}
        tool = str((content or {}).get("tool") or "").strip()
        if not tool or not _is_writeish_tool(tool):
            # keep edition.record / replace_page as "AI actions" too when near end
            if tool not in (
                "edition.record_effective_change",
                "corpus.replace_page_blocks",
                "corpus.patch_block",
                "corpus.save_blocks",
            ):
                continue
        args = (content or {}).get("args") if isinstance(content, dict) else {}
        if not isinstance(args, dict):
            args = {}
        path = str(
            args.get("path")
            or args.get("file")
            or args.get("target")
            or args.get("edition_id")
            or ""
        )
        title = str((content or {}).get("title") or "")
        summary = f"{title} {path}".strip() or json.dumps(args, ensure_ascii=False)[:160]
        key = f"{tool}|{summary[:80]}"
        if key in seen:
            continue
        seen.add(key)
        tools.append({"tool": tool, "args_summary": summary[:300]})
        if len(tools) >= max_tools:
            break

    primary_user = users[-1] if users else ""
    ai_summary = assists[-1] if assists else ""
    narrative_parts = []
    if primary_user:
        narrative_parts.append("用户要求：" + primary_user[:400])
    if ai_summary:
        narrative_parts.append("AI 随后：" + ai_summary[:500])
    narrative_parts.append("结果：文件守卫检出越权改动并回滚。")

    # Collect all user texts in window for page_no (focus may be earlier than short ask)
    all_user_texts = [
        _text_of(m.get("content"))
        for m in window
        if str(m.get("role") or "") == "user"
    ]
    return _with_focus_meta(
        {
            "user_requests": users,
            "user_input": primary_user,
            "ai_summaries": assists,
            "ai_summary": ai_summary,
            "ai_tools": tools,
            "guard_seq": guard_seq,
            "narrative": "\n\n".join(narrative_parts)[:2500],
        },
        extra_texts=all_user_texts,
    )


def trace_fs_violation(
    messages: list[dict[str, Any]],
    *,
    paths: list[str] | None = None,
    detail: str = "",
    near_seq: int | None = None,
) -> dict[str, Any]:
    """End-to-end: locate guard message then walk upstream."""
    seq = find_guard_seq(
        messages, paths=paths, detail=detail, near_seq=near_seq
    )
    if seq is None:
        # Fall back: last user + last assistant in session
        users = []
        assists = []
        for m in messages:
            role = str(m.get("role") or "")
            text = _text_of(m.get("content"))
            if role == "user" and _is_substantive_user(text):
                users.append(text)
            elif role == "assistant" and len(text) >= 20:
                assists.append(text[:1500])
        primary = users[-1] if users else ""
        ai = assists[-1] if assists else ""
        return _with_focus_meta(
            {
                "user_requests": users,
                "user_input": primary,
                "ai_summaries": assists[-2:],
                "ai_summary": ai,
                "ai_tools": [],
                "guard_seq": 0,
                "narrative": (
                    ("用户要求：" + primary[:400] + "\n\n" if primary else "")
                    + ("AI 随后：" + ai[:500] + "\n\n" if ai else "")
                    + "结果：文件守卫检出越权改动并回滚。"
                )[:2500],
            }
        )
    return trace_before_guard(messages, seq)
