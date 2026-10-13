"""Look at printed TOC page images, compare MinerU, write toc_printed.json."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app import jobs, library, prompts_store, sdk_runner

try:
    import toc_mineru as mineru_lib
except Exception:  # pragma: no cover
    mineru_lib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

LOCATE_LIMIT = 16
PROMPT_LOCATE = "prompts/toc_locate.md"
PROMPT_READ = "prompts/toc_read.md"
PROMPT_RECONCILE = "prompts/toc_reconcile.md"
_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)
_WS = re.compile(r"\s+")


class TocEntryModel(BaseModel):
    label: str
    level: int = 1
    is_chapter: bool = False
    print_page: int | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("empty label")
        return text

    @field_validator("level")
    @classmethod
    def _level(cls, value: int) -> int:
        return 0 if int(value) <= 0 else 1

    @field_validator("print_page")
    @classmethod
    def _page(cls, value: int | None) -> int | None:
        if value is None:
            return None
        page = int(value)
        return page if page > 0 else None


class LocateOut(BaseModel):
    toc_pages: list[int] = Field(default_factory=list)

    @field_validator("toc_pages")
    @classmethod
    def _pages(cls, value: list[int]) -> list[int]:
        out: list[int] = []
        for raw in value:
            try:
                page = int(raw)
            except (TypeError, ValueError):
                continue
            if page > 0 and page not in out:
                out.append(page)
        return out


class ReadOut(BaseModel):
    entries: list[TocEntryModel] = Field(default_factory=list)


class VerdictModel(BaseModel):
    label: str
    action: Literal["keep", "fix", "drop", "add"]
    note: str = ""


class ReconcileOut(BaseModel):
    toc_pages: list[int] = Field(default_factory=list)
    entries: list[TocEntryModel] = Field(default_factory=list)
    verdicts: list[VerdictModel] = Field(default_factory=list)
    winner: Literal["mineru", "agent"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def printed_path(book_id: str) -> Path:
    return library.book_dir(book_id) / "toc_printed.json"


def load_printed(book_id: str) -> dict[str, Any]:
    path = printed_path(book_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_printed(book_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = printed_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["book_id"] = book_id
    body["updated_at"] = _now()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return body


def parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fence = _FENCE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


def dump_entries(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（空）"
    lines: list[str] = []
    for row in rows:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        page = row.get("print_page")
        extra = f"  印{page}" if page is not None else ""
        level = int(row.get("level") or (0 if row.get("is_chapter") else 1))
        indent = "" if level == 0 else "  "
        lines.append(f"{indent}{label}{extra}")
    return "\n".join(lines) if lines else "（空）"


def entry_key(row: dict[str, Any]) -> tuple[str, int, int | None]:
    label = _WS.sub("", str(row.get("label") or ""))
    if "level" in row and row.get("level") is not None:
        try:
            level = int(row["level"])
        except (TypeError, ValueError):
            level = 0 if row.get("is_chapter") else 1
    else:
        level = 0 if row.get("is_chapter") else 1
    page = row.get("print_page")
    try:
        page_i = int(page) if page is not None else None
    except (TypeError, ValueError):
        page_i = None
    return (label, 0 if level <= 0 else 1, page_i)


def trees_match(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    if not left or not right:
        return False
    return {entry_key(row) for row in left} == {entry_key(row) for row in right}


def entries_as_dicts(rows: list[TocEntryModel]) -> list[dict[str, Any]]:
    return [row.model_dump() for row in rows]


def assemble_locate_user(*, page_count: int, attached: list[int], missing: list[int]) -> str:
    parts = [
        f"全书 {page_count} 页。下面附图是 PDF 第 {', '.join(str(n) for n in attached) or '无'} 页。",
        "只输出 toc_pages。不要解释。",
    ]
    if missing:
        parts.append(f"缺图页（不要猜）：{', '.join(str(n) for n in missing)}")
    return "\n".join(parts)


def assemble_read_user(*, toc_pages: list[int]) -> str:
    pages = ", ".join(str(n) for n in toc_pages)
    return (
        f"目录页原图已附上，PDF 页：{pages}。\n"
        "只根据图抽出 entries。不要使用 MinerU，不要提 MinerU。不要解释。"
    )


def assemble_reconcile_user(*, toc_pages: list[int], mineru_entries: list[dict[str, Any]]) -> str:
    pages = ", ".join(str(n) for n in toc_pages)
    return (
        f"同一几页原图，PDF 页：{pages}。\n"
        "MinerU 清单（只供对照）：\n"
        f"{dump_entries(mineru_entries)}\n"
        "按印刷栏点名后输出 verdicts、winner、entries。不要解释。"
    )


def read_prompt(rel: str) -> str:
    try:
        return prompts_store.read_file(rel)
    except (FileNotFoundError, ValueError):
        return ""


def page_images(book_id: str, pages: list[int]) -> tuple[list[dict[str, Any]], list[int], list[int]]:
    attached: list[dict[str, Any]] = []
    have: list[int] = []
    missing: list[int] = []
    for page_no in pages:
        raw = library.get_page_image(book_id, page_no)
        if not raw:
            missing.append(page_no)
            continue
        have.append(page_no)
        attached.append(
            {
                "data": base64.b64encode(raw).decode("ascii"),
                "mime_type": "image/jpeg",
                "name": f"p{int(page_no):04d}.jpg",
            }
        )
    return attached, have, missing


def _ask(
    system: str,
    user: str,
    images: list[dict[str, Any]],
    job_id: str,
    *,
    workspace_tools: bool = True,
) -> str:
    body = f"{system.rstrip()}\n\n{user}" if system else user
    text, _sdk_id = sdk_runner._prompt_sdk(
        body,
        conversation_id="",
        resume_id="",
        job_id=job_id,
        mode="ask",
        images=images,
        workspace_tools=workspace_tools,
    )
    return text or ""


def _trace(job_id: str, title: str, detail: str) -> None:
    if job_id:
        jobs.append_trace(job_id, "tool", title, detail[:4000])


def _live_job() -> str:
    try:
        from app.context import live_sdk_job

        return live_sdk_job() or ""
    except ImportError:
        from app.context import current_job_id

        return current_job_id.get() or ""


def harvest(book_id: str) -> dict[str, Any]:
    book = library.book_dir(book_id)
    if mineru_lib is None:
        return {
            "toc_pages": [],
            "entries": [],
            "glued_pages": [],
            "index_hits": 0,
            "quality": "weak",
        }
    return mineru_lib.harvest_mineru_catalog(book)


def locate_pages(book_id: str, page_count: int, job_id: str) -> list[int]:
    limit = min(max(int(page_count or 0), 0), LOCATE_LIMIT)
    want = list(range(1, limit + 1))
    images, have, missing = page_images(book_id, want)
    user = assemble_locate_user(page_count=page_count, attached=have, missing=missing)
    if not images:
        raise ValueError("前几页没有原图，无法定位目录页")
    reply = _ask(read_prompt(PROMPT_LOCATE), user, images, job_id)
    _trace(job_id, "toc_locate", reply[:1500])
    try:
        parsed = LocateOut.model_validate(parse_json_object(reply))
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ValueError(f"定位目录页失败：{exc}") from exc
    return [page for page in parsed.toc_pages if page <= page_count]


def read_pages(book_id: str, toc_pages: list[int], job_id: str) -> list[dict[str, Any]]:
    images, have, missing = page_images(book_id, toc_pages)
    if missing:
        raise ValueError(f"目录页缺原图：{missing}")
    if not images:
        raise ValueError("目录页没有原图")
    user = assemble_read_user(toc_pages=have)
    reply = _ask(read_prompt(PROMPT_READ), user, images, job_id)
    _trace(job_id, "toc_read", reply[:2000])
    try:
        parsed = ReadOut.model_validate(parse_json_object(reply))
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ValueError(f"看图抽目录失败：{exc}") from exc
    return entries_as_dicts(parsed.entries)


def reconcile_pages(
    book_id: str,
    toc_pages: list[int],
    mineru_entries: list[dict[str, Any]],
    job_id: str,
) -> ReconcileOut:
    images, have, missing = page_images(book_id, toc_pages)
    if missing:
        raise ValueError(f"核对缺原图：{missing}")
    user = assemble_reconcile_user(toc_pages=have, mineru_entries=mineru_entries)
    reply = _ask(
        read_prompt(PROMPT_RECONCILE),
        user,
        images,
        job_id,
        workspace_tools=False,
    )
    _trace(job_id, "toc_reconcile", reply[:2000])
    try:
        return ReconcileOut.model_validate(parse_json_object(reply))
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ValueError(f"核对目录失败：{exc}") from exc


def write_printed_ok(
    book_id: str,
    *,
    toc_pages: list[int],
    entries: list[dict[str, Any]],
    quality: str,
    mineru_entries: list[dict[str, Any]],
    agent_entries: list[dict[str, Any]],
    winner: str,
    verdicts: list[dict[str, Any]],
    passes: list[str],
) -> dict[str, Any]:
    payload = write_printed(
        book_id,
        {
            "ok": True,
            "toc_pages": toc_pages,
            "entries": entries,
            "mineru_quality": quality,
            "mineru_entries": mineru_entries,
            "agent_entries": agent_entries,
            "winner": winner,
            "verdicts": verdicts,
            "passes": list(passes),
            "brief": f"winner={winner} quality={quality} n={len(entries)}",
        },
    )
    payload["entry_count"] = len(entries)
    return payload


def run_book(book_id: str, job_id: str = "") -> dict[str, Any]:
    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    meta = library.get_book(sid)
    if meta is None:
        raise ValueError("book not found")
    page_count = int(meta.get("page_count") or 0)
    if page_count < 1:
        pages_dir = library.book_dir(sid) / "pages"
        page_count = len(list(pages_dir.glob("*.md"))) if pages_dir.is_dir() else 0
    mined = harvest(sid)
    quality = str(mined.get("quality") or "weak")
    mineru_entries = list(mined.get("entries") or [])
    _trace(job_id, "resolve_printed_toc", f"MinerU quality={quality} entries={len(mineru_entries)}")

    try:
        mined_pages = [int(p) for p in (mined.get("toc_pages") or []) if int(p) > 0]
        if mined_pages:
            toc_pages = mined_pages
            _trace(job_id, "toc_locate", f"MinerU 已标目录页 {toc_pages}，跳过前 16 页看图")
        else:
            toc_pages = locate_pages(sid, page_count, job_id)
        if not toc_pages:
            raise ValueError("没有标出印刷目录页")
        agent_entries = read_pages(sid, toc_pages, job_id)
        if not agent_entries:
            if quality == "strong" and mineru_entries:
                payload = write_printed(
                    sid,
                    {
                        "ok": True,
                        "toc_pages": toc_pages,
                        "entries": mineru_entries,
                        "mineru_quality": quality,
                        "winner": "mineru",
                        "passes": ["locate", "read-empty"],
                        "brief": "看图为空，MinerU 为强，采用 MinerU。",
                    },
                )
                payload["entry_count"] = len(mineru_entries)
                return payload
            raise ValueError("看图没有抽出目录条目")

        winner = "agent"
        passes = ["locate", "read"]
        entries = agent_entries
        verdicts: list[dict[str, Any]] = []
        if quality == "strong" and trees_match(agent_entries, mineru_entries):
            winner = "mineru"
            entries = mineru_entries
        elif quality == "strong" and mineru_entries:
            try:
                checked = reconcile_pages(sid, toc_pages, mineru_entries, job_id)
                passes.append("reconcile")
                winner = checked.winner
                if checked.entries:
                    entries = entries_as_dicts(checked.entries)
                if checked.toc_pages:
                    toc_pages = [p for p in checked.toc_pages if p > 0] or toc_pages
                verdicts = [row.model_dump() for row in checked.verdicts]
                if winner == "mineru" and not checked.entries:
                    entries = mineru_entries
            except ValueError as exc:
                _trace(job_id, "toc_reconcile", str(exc))
                winner = "agent"
                entries = agent_entries
        return write_printed_ok(
            sid,
            toc_pages=toc_pages,
            entries=entries,
            quality=quality,
            mineru_entries=mineru_entries,
            agent_entries=agent_entries,
            winner=winner,
            verdicts=verdicts,
            passes=passes,
        )
    except Exception as exc:
        payload = write_printed(
            sid,
            {
                "ok": False,
                "error": str(exc)[:800],
                "toc_pages": [],
                "entries": [],
                "mineru_quality": quality,
                "mineru_entries": mineru_entries,
                "winner": "",
                "passes": [],
                "brief": str(exc)[:200],
            },
        )
        payload["ok"] = False
        return payload


def _holds_foreign_bridge(job_id: str) -> bool:
    row = jobs.get_job(job_id) if job_id else None
    kind = str((row or {}).get("kind") or "")
    if kind in {"chat", "grade", "page_refine", "task"}:
        return True
    return jobs.count_sdk_active(exclude=job_id) > 0


def resolve_printed_toc(book_id: str, root: str = "") -> dict:
    """看原图判定印刷目录页并抽出骨架，与 MinerU 对照后写入 toc_printed.json。"""
    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    job_id = _live_job()
    if _holds_foreign_bridge(job_id):
        existing = load_printed(sid)
        if existing.get("ok") and existing.get("entries"):
            return existing
        raise ValueError(
            "对话里不能再开一座看图桥。印刷目录由独立作业 printed_toc 看原图，不要在聊天任务里调用 resolve_printed_toc。"
        )
    return run_book(sid, job_id)
