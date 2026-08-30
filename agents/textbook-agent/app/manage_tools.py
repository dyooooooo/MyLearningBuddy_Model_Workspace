from __future__ import annotations

import json
from typing import Any

from app import checkpoints, config_store, hooks_store, plugins_store, prompts_store, surfaces


def list_config() -> dict[str, Any]:
    """列出当前技能、规则、Python 工具、外接、提示词、钩子和插件名称。不含文件正文。"""
    snap = config_store.snapshot()
    tools = []
    for item in (snap.get("tools") or {}).get("tools") or []:
        if not isinstance(item, dict):
            continue
        tools.append(
            {
                "id": item.get("id"),
                "path": item.get("path"),
                "description": item.get("description"),
            }
        )
    mcp = snap.get("mcp") or {}
    servers = mcp.get("mcpServers") if isinstance(mcp, dict) else {}
    hooks = (snap.get("hooks") or {}).get("hooks") or {}
    return {
        "generation": snap.get("generation"),
        "mcp": list(servers) if isinstance(servers, dict) else [],
        "skills": [s.get("id") for s in snap.get("skills") or [] if isinstance(s, dict)],
        "rules": [r.get("id") for r in snap.get("rules") or [] if isinstance(r, dict)],
        "tools": tools,
        "prompts": [p.get("id") for p in snap.get("prompts") or [] if isinstance(p, dict)],
        "hooks": list(hooks) if isinstance(hooks, dict) else [],
        "plugins": [p.get("id") for p in snap.get("plugins") or [] if isinstance(p, dict)],
    }


def write_tool(tool_id: str, content: str, description: str = "", path: str = "") -> dict[str, Any]:
    """写入一个 Python 工具并登记到 manifest。函数名必须等于 tool_id。"""
    row = config_store.put_tool(tool_id, path=path, description=description, content=content)
    return {
        "id": row.get("id"),
        "path": row.get("path"),
        "description": row.get("description"),
        "bytes": len(content or ""),
    }


def write_skill(skill_id: str, content: str) -> dict[str, str]:
    """写入一条技能（.cursor/skills/<id>/SKILL.md）。"""
    return config_store.put_skill(skill_id, content)


def write_rule(rule_id: str, content: str) -> dict[str, str]:
    """写入一条规则（.cursor/rules/<id>.mdc）。"""
    return config_store.put_rule(rule_id, content)


def reload_config() -> dict[str, int]:
    """让下一轮对话立刻使用刚写入的技能/规则/工具。"""
    from app.change import after_write

    return {"generation": after_write("config", "reload")}


def list_surfaces() -> dict:
    """列出书页/目录树/对照三个画面插槽是否加载、金样结果。"""
    return surfaces.list_slots()


def write_surface(slot: str, path: str, content: str) -> dict:
    """写入某个画面插槽文件（prepare.mjs / theme.css / 金样等）。不自动上线。"""
    return surfaces.write_surface(slot, path, content)


def test_surface(slot: str = "") -> dict:
    """对工作区插槽跑金样，返回每条 diff。空 slot 表示三个都测。"""
    return surfaces.test_surface(slot)


def reload_surfaces() -> dict:
    """金样全绿才切到新渲染；失败则继续用上一份通过的或种子。"""
    return surfaces.reload_surfaces()


def write_prompt(name: str, content: str) -> dict:
    """写入一段系统提示词（prompts/<name>.md，含 page_refine / tasks）。"""
    return prompts_store.put_prompt(name, content)


def list_hooks() -> dict:
    """列出 hooks.json 事件与 .cursor/hooks/ 脚本名。"""
    return hooks_store.snapshot()


def write_hook(event: str, entry: str) -> dict:
    """登记一条钩子。entry 是 JSON，例如 {"type":"command","command":".cursor/hooks/x.sh","failClosed":true}。"""
    try:
        data = json.loads(entry) if isinstance(entry, str) else entry
    except json.JSONDecodeError as exc:
        raise ValueError(f"entry must be JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("entry must be a JSON object")
    return hooks_store.add_entry(event, data)


def write_hook_script(name: str, content: str) -> dict:
    """写入 .cursor/hooks/<name> 并加上可执行位。"""
    return hooks_store.put_hook_script(name, content)


def list_plugins() -> dict:
    """列出已装插件（本地目录与停用目录）。"""
    return {"plugins": plugins_store.list_plugins(), "allowlist": plugins_store.read_allowlist()}


def checkpoint(note: str = "") -> dict:
    """立刻打一个检查点，之后可以 restore_checkpoint 回去。"""
    return checkpoints.snapshot("manual", note or "manual")


def list_checkpoints(limit: int = 20) -> dict:
    """列出最近的检查点（时间、类型、改了哪些文件）。"""
    return {"checkpoints": checkpoints.list_checkpoints(limit)}


def restore_checkpoint(ref: str) -> dict:
    """回到某个检查点。默认不动 traces/ 和 .cursor/projects/，聊天记录会留下。"""
    return checkpoints.restore(ref)


def request_code_edit(
    path: str,
    situation: str,
    why: str,
    changes: str,
    impact: str,
    scope: str = "",
) -> dict[str, Any]:
    """提交代码修改申请。四段说明缺一不可，通过后停下等用户在卡片上确认。"""
    from app import approvals, jobs
    from app.context import current_job_id

    rel = approvals.norm_path(path)
    got_scope = (scope or "").strip() or approvals.infer_scope(rel)
    if not got_scope:
        return {
            "ok": False,
            "missing": ["path"],
            "hint": "只能申请改 tools/*.py、.cursor/hooks/* 或 surfaces/*。补齐后重提，不要直接写文件。",
        }
    fields = {
        "situation": situation,
        "why": why,
        "changes": changes,
        "impact": impact,
    }
    missing = approvals.validate(fields)
    if missing:
        return {
            "ok": False,
            "missing": missing,
            "hint": "四段说明各至少 20 字：实际情况、为什么要改、具体改哪些地方、改完的影响。补齐后重提，不要直接写文件。",
        }
    job_id = current_job_id.get() or ""
    conversation_id = ""
    if job_id:
        row = jobs.get_job(job_id) or {}
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        conversation_id = str((payload or {}).get("conversation_id") or "")
    record = approvals.open_request(
        rel,
        situation,
        why,
        changes,
        impact,
        scope=got_scope,
        job_id=job_id,
        conversation_id=conversation_id,
    )
    if job_id:
        jobs.append_trace(
            job_id,
            "approval",
            "需要授权",
            json.dumps(record, ensure_ascii=False),
            persist=True,
        )
    return {
        "ok": True,
        "req_id": record["req_id"],
        "path": record["path"],
        "scope": record["scope"],
        "next": "本轮到此为止，等用户在授权卡上确认。不要直接写文件，也不要把对话里的话当成许可。",
    }


BUILTINS = (
    list_config,
    request_code_edit,
    write_tool,
    write_skill,
    write_rule,
    reload_config,
    list_surfaces,
    write_surface,
    test_surface,
    reload_surfaces,
    write_prompt,
    list_hooks,
    write_hook,
    write_hook_script,
    list_plugins,
    checkpoint,
    list_checkpoints,
    restore_checkpoint,
)
