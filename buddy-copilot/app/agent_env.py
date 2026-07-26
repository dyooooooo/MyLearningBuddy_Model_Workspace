"""Agent 环境装配：规则、技能、资源地图的加载与上下文包组装。

设计原则（对齐方案「Agent 环境工程」）：
- 规则/技能/资源地图全部随 buddy-copilot 版本化，不依赖 ambient 设置；
- 每次会话按意图注入对应技能，规则全量注入；
- 资源地图（resources.yaml）让 Agent 直接知道「东西在哪、怎么访问、能不能改」。
"""

from __future__ import annotations

from pathlib import Path

# 意图 → 技能文件
SKILL_BY_KIND = {
    "import_advice": ["edition-tuning.md", "verify-loop.md"],
    "page_assist": ["page-diagnosis.md", "edition-tuning.md", "verify-loop.md"],
}


def _read_all(directory: Path, names: list[str] | None = None) -> str:
    if not directory.is_dir():
        return ""
    files = (
        [directory / n for n in names]
        if names is not None
        else sorted(directory.glob("*.md"))
    )
    parts = []
    for f in files:
        if f.is_file():
            parts.append(f"<!-- {f.name} -->\n" + f.read_text(encoding="utf-8"))
    return "\n\n---\n\n".join(parts)


def load_rules(agent_env_dir: Path) -> str:
    return _read_all(agent_env_dir / "rules")


def load_skills(agent_env_dir: Path, kind: str) -> str:
    names = SKILL_BY_KIND.get(kind, [])
    return _read_all(agent_env_dir / "skills", names)


def load_resource_map(agent_env_dir: Path) -> str:
    f = agent_env_dir / "resources.yaml"
    return f.read_text(encoding="utf-8") if f.is_file() else ""


def load_context_maps(agent_env_dir: Path) -> str:
    return _read_all(agent_env_dir / "context")


def build_context_pack(agent_env_dir: Path, kind: str, task_context: str) -> str:
    """组装一次会话的完整初始 prompt 前缀。"""
    sections = [
        "# Buddy Copilot 会话环境\n\n以下规则、资源地图与技能是本次任务的强约束与操作手册。",
        "## 规则（必须遵守）\n\n" + load_rules(agent_env_dir),
        "## 资源地图（所有资源以此为准，禁止乱找）\n\n```yaml\n"
        + load_resource_map(agent_env_dir)
        + "\n```",
        "## Pipeline 地图（代码环境是确定的，按图索骥）\n\n" + load_context_maps(agent_env_dir),
        "## 技能（本任务的操作手册）\n\n" + load_skills(agent_env_dir, kind),
        "## 任务上下文（编排器注入的实时数据）\n\n" + task_context,
    ]
    return "\n\n".join(sections)


def verify_resource_paths(agent_env_dir: Path, workspace: Path) -> list[str]:
    """一致性守护：资源地图/上下文文档里引用的工作区路径是否仍存在。

    返回失效路径列表（空 = 全部有效）。
    """
    import re

    stale: list[str] = []
    candidates: set[str] = set()
    for f in list((agent_env_dir / "context").glob("*.md")) + [agent_env_dir / "resources.yaml"]:
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"`([\w./-]+/[\w./*-]+)`", text):
            candidates.add(m.group(1))
        for m in re.finditer(r"^\s+(?:path|where):\s*([\w./-]+)", text, re.M):
            candidates.add(m.group(1))
    # 只校验以工作区已知顶层目录开头的路径（排除 MinIO 前缀、文档内相对片段等）
    top_dirs = ("learning-buddy-api/", "exam-ocr-server/", "configs/", "doc/", "buddy-copilot/")
    for c in sorted(candidates):
        if not c.startswith(top_dirs):
            continue
        base = c.split("*", 1)[0].rstrip("/")
        p = workspace / base
        if not p.exists() and not p.parent.exists():
            stale.append(c)
    return stale
