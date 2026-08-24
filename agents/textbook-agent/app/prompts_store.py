"""Workspace-editable system prompts with seed fallback."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.workspace import ensure_workspace

logger = logging.getLogger(__name__)

MAX_CHARS = 32_768
NAMES = ("chat", "plan", "ask", "grade", "title")
MODE_TO_NAME = {"agent": "chat", "plan": "plan", "ask": "ask"}

SEEDS: dict[str, str] = {
    "chat": """你是书库工作区里的通用 Agent。对工作区（textbooks/、traces/、.cursor/、tools/、inbox/、prompts/、surfaces/）拥有读、写、删、改的全部权限。工作区以外（包括 /app 源码和系统）绝对不能碰。

红线（钩子会拦，不要去碰）：
- .cursor/projects/（会话存储，删了对话就显示不出来）
- .checkpoints.git/（回退能力本身）

可以：
1. 查、改、删 textbooks/ 里的书、分页 Markdown 和原版 PDF。
2. 改技能、规则、Python 工具、外接 MCP、提示词、钩子、插件。
3. 打检查点、回退、分叉。
4. 整理 traces/ 里的过往轨迹。
没有教材时不要用通识冒充书上内容。

改工具时：先 list_config，再 write_tool（函数名必须等于工具编号，并登记到 manifest），最后 reload_config。
先读 .cursor/skills/manage-self/SKILL.md。

用户：
""",
    "plan": """你是规划模式（Plan）。先调查工作区再写出可执行计划，这一轮不要改 textbooks/、.cursor/、tools/、prompts/。工作区以外（包括 /app）绝对不能碰。
没有教材时不要用通识冒充书上内容。先读 .cursor/skills/manage-self/SKILL.md。

用户：
""",
    "ask": """你是问答模式（Ask）。只读、解释、给建议，禁止改任何文件（包括 textbooks/、.cursor/、tools/、traces/、inbox/、prompts/）。工作区以外（包括 /app）绝对不能碰。
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


def get(name: str) -> str:
    key = (name or "").strip()
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
    for name in NAMES:
        path = _path(name)
        live = ""
        source = "seed"
        try:
            live = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            live = ""
        if live.strip() and len(live) <= MAX_CHARS:
            source = "workspace"
        out.append(
            {
                "id": name,
                "content": get(name) if name != "title" else (live.strip() and live or SEEDS[name]),
                "source": source,
                "seed": SEEDS[name],
            }
        )
    return out


def put_prompt(name: str, content: str) -> dict[str, Any]:
    ensure_workspace()
    key = (name or "").strip()
    if key not in SEEDS:
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
    if key not in SEEDS:
        raise ValueError(f"unknown prompt: {name}")
    path = _path(key)
    if not path.exists():
        return False
    path.unlink()
    ensure_prompt_files()
    from app.change import after_write

    after_write("prompts", f"restore prompt {key}")
    return True
