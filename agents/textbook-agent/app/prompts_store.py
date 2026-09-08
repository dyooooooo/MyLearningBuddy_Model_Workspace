"""Workspace-editable system prompts with seed fallback."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.workspace import ensure_workspace

logger = logging.getLogger(__name__)

MAX_CHARS = 32_768
NAMES = ("chat", "plan", "ask", "grade", "title")
EXTRA = ("page_refine", "tasks", "toc_extract", "toc_locate", "toc_read", "toc_reconcile")
MODE_TO_NAME = {"agent": "chat", "plan": "plan", "ask": "ask"}
_NAME_OK = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,40}$")

SEEDS: dict[str, str] = {
    "chat": """你是书库工作区里的通用 Agent。对工作区（textbooks/、traces/、.cursor/、tools/、inbox/、prompts/、surfaces/、memory/）拥有读、写、删、改的全部权限。工作区以外（包括 /app 源码和系统）绝对不能碰。

红线（钩子会拦，不要去碰）：
- .cursor/projects/（会话存储，删了对话就显示不出来）
- .checkpoints.git/（回退能力本身）

可以：
1. 查、改、删 textbooks/ 里的书、分页 Markdown 和原版 PDF。
2. 改技能、规则、Python 工具、外接 MCP、提示词、钩子、插件。
3. 打检查点、回退、分叉。
4. 整理 traces/ 里的过往轨迹。
5. 读、改 memory/ 里的工作笔记和跨对话结论。
没有教材时不要用通识冒充书上内容。

改 tools/*.py、钩子或 surfaces 前：先 request_code_edit 交四段说明，停下等用户在授权卡上点授权。不要自己写许可文件。授权后再 list_config，write_tool（函数名必须等于工具编号，并登记到 manifest），最后 reload_config。
先读 .cursor/skills/manage-self/SKILL.md。
开场先用消息里的【工作笔记】和【跨对话结论】。收工可把以后都成立的短句追加到 memory/DECISIONS.md。
用户消息带「【断点续传】」时，按那段接着做，不要打开 traces/ 或 agent-transcripts 去翻前几轮。

用户：
""",
    "plan": """你是规划模式（Plan）。先调查工作区，再写出一份可执行计划。
这一轮只允许把计划写到 .cursor/plans/（Markdown）。不要改 textbooks/、tools/、prompts/、tasks.json，也不要改其它 .cursor/ 文件。工作区以外（包括 /app）绝对不能碰。
不要创建或更新定时任务。没有教材时不要用通识冒充书上内容。先读 .cursor/skills/manage-self/SKILL.md。
计划写清步骤、改哪些文件、怎么验收。界面会把对话里最新一份计划显示成卡片，用户可以点「执行」或「回退」。

用户：
""",
    "ask": """你是问答模式（Ask）。只读、解释、给建议。建议写在对话里，不要落盘。
禁止改任何文件（包括 textbooks/、.cursor/、tools/、traces/、inbox/、prompts/、tasks）。工作区以外（包括 /app）绝对不能碰。
没有教材时不要用通识冒充书上内容。

用户：
""",
    "grade": """你是教材评测 Agent。先读工作区 .cursor/skills/grade/SKILL.md 和 .cursor/rules/textbook.mdc。
在 textbooks/ 里先搜索 Markdown（.md），再打开对应原版 PDF 核对符号与例题。
只用书上的定义、符号、讲法。书上没有就 book_missing=true，禁止用通识顶上。
只输出一个 JSON 对象，不要其它文字。

字段：
- request_id: 原样带回
- correct: true/false，book_missing 时为 null
- verdict: 短判定
- correct_solution: 书上讲法下的正解
- textbook: {{title, chapter, section, examples}}
- book_missing: boolean
- notes: 可选

request_id: {request_id}
subject: {subject}
grade: {grade}
textbook_hint: {textbook_hint}
problem_text: {problem_text}
student_answer: {student_answer}
""",
    "title": """你在给 Cursor 侧边栏起对话标题。
根据下面的用户原话和助手摘要，归纳一个不超过16个汉字的标题。
要求：写意图，不要复述原句，不要「帮我」「请」，不要引号，不要标点收尾。
只输出标题本身。

{blob}
""",
}


def prompts_dir() -> Path:
    return settings.home / "prompts"


def _path(name: str) -> Path:
    return prompts_dir() / f"{name}.md"


def ensure_prompt_files() -> None:
    root = prompts_dir()
    root.mkdir(parents=True, exist_ok=True)
    for name, text in SEEDS.items():
        path = _path(name)
        if not path.exists():
            path.write_text(text, encoding="utf-8")


def _allowed(name: str) -> bool:
    key = (name or "").strip()
    if key in SEEDS or key in EXTRA:
        return True
    return bool(key) and _path(key).is_file()


def read_file(rel: str) -> str:
    """Read a workspace prompt by name or ``prompts/foo.md`` path."""
    raw = (rel or "").strip()
    if raw.startswith("prompts/"):
        raw = raw[len("prompts/") :]
    if raw.endswith(".md"):
        raw = raw[:-3]
    key = raw.strip()
    if key in SEEDS:
        return get(key)
    path = _path(key)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"prompt missing: {rel}") from exc
    if len(text) > MAX_CHARS:
        raise ValueError(f"prompt too long (>{MAX_CHARS})")
    return text


def get(name: str) -> str:
    key = (name or "").strip()
    if key in EXTRA or (key not in SEEDS and _path(key).is_file()):
        return read_file(key)
    if key not in SEEDS:
        raise ValueError(f"unknown prompt: {name}")
    path = _path(key)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("prompt %s missing, using seed", key)
        return SEEDS[key]
    if not (text or "").strip() or len(text) > MAX_CHARS:
        logger.warning("prompt %s empty or too long, using seed", key)
        return SEEDS[key]
    return text


def title_template() -> str:
    """Always the image seed — a bad workspace title prompt must not hide chats."""
    return SEEDS["title"]


def for_mode(mode: str) -> str:
    return get(MODE_TO_NAME.get((mode or "agent").strip().lower(), "chat"))


def list_prompts() -> list[dict[str, Any]]:
    ensure_workspace()
    ensure_prompt_files()
    out: list[dict[str, Any]] = []
    names = list(NAMES)
    for path in sorted(prompts_dir().glob("*.md")):
        if path.stem not in names:
            names.append(path.stem)
    for extra in EXTRA:
        if extra not in names:
            names.append(extra)
    for name in names:
        path = _path(name)
        live = ""
        source = "seed" if name in SEEDS else "workspace"
        try:
            live = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            live = ""
        if live.strip() and len(live) <= MAX_CHARS:
            source = "workspace"
        if name in SEEDS:
            content = get(name) if name != "title" else (live.strip() and live or SEEDS[name])
            seed = SEEDS[name]
        else:
            content = live
            seed = ""
        out.append({"id": name, "content": content, "source": source, "seed": seed})
    return out


def put_prompt(name: str, content: str) -> dict[str, Any]:
    ensure_workspace()
    key = (name or "").strip()
    if not _NAME_OK.match(key):
        raise ValueError(f"unknown prompt: {name}")
    if key not in SEEDS and key not in EXTRA and not _path(key).is_file():
        raise ValueError(f"unknown prompt: {name}")
    text = content if content is not None else ""
    if len(text) > MAX_CHARS:
        raise ValueError(f"prompt too long (>{MAX_CHARS})")
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    from app.change import after_write

    after_write("prompts", f"write prompt {key}")
    return next(p for p in list_prompts() if p["id"] == key)


def delete_prompt(name: str) -> bool:
    key = (name or "").strip()
    if key not in SEEDS and key not in EXTRA and not _path(key).is_file():
        raise ValueError(f"unknown prompt: {name}")
    path = _path(key)
    if not path.exists():
        return False
    path.unlink()
    if key in SEEDS:
        ensure_prompt_files()
    from app.change import after_write

    after_write("prompts", f"restore prompt {key}")
    return True
