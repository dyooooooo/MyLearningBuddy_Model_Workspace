"""系统工具插件：文件修改申请、词插件专属闸、Docker 重建、MinIO 预检、资源地图。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx

from ...permissions import load_permissions
from ..docker_ops import rebuild_buddy_services, verify_file_in_container
from ..fs_guard import gated_prefix, is_whitelisted
from ..registry import ToolContext, ToolSpec, register

_PLUGIN_SUFFIX = ".py"
# 未审草稿的落脚处：加载器不扫这里，所以写进去不会被执行。
_PROPOSALS = "proposals/"


def _run_plugin_check(api_dir: Path, target: Path) -> dict[str, Any]:
    """在 learning-buddy-api 的 venv 里跑一遍插件自检（沙箱执行正反例，不写盘）。"""
    py = api_dir / ".venv" / "bin" / "python"
    try:
        proc = subprocess.run(
            [str(py), "-m", "app.services.vocab_plugin_check", str(target)],
            cwd=api_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"自检执行失败: {exc}"}
    try:
        reports = json.loads(proc.stdout)
    except ValueError:
        return {
            "ok": False,
            "error": "自检没给出 JSON 结果",
            "output": (proc.stdout + "\n" + proc.stderr)[-2000:],
        }
    return {"ok": proc.returncode == 0, "reports": reports}


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    perms = load_permissions(ctx.settings.agent_env_dir)
    gated = perms.gated_subpaths

    def fs_request_edit(paths: list[str], reason: str) -> Any:
        """申请修改工作区文件（闸门：需用户批准）。paths 为相对工作区根的文件路径列表，
        必须全部在白名单内；批准后你才可以直接编辑这些文件。"""
        blocked = [p for p in paths if gated_prefix(str(p), gated)]
        if blocked:
            return {
                "approved": False,
                "error": (
                    "以下路径要走词插件专属闸，不能用 fs.request_edit: "
                    + ", ".join(blocked)
                ),
                "hint": (
                    "那个目录里的 .py 会在 buddy-worker 进程里执行。草稿先写"
                    "configs/checks/vocab_ext/proposals/（本工具即可），"
                    "用 vocab.check_plugin 跑过正反例，再用 vocab.request_plugin_edit "
                    "申请放进会被执行的目录。"
                ),
            }
        bad = [
            p for p in paths
            if not is_whitelisted(str(p), perms.write_whitelist, perms.forbidden, gated)
        ]
        if bad:
            return {
                "approved": False,
                "error": "以下路径不在文件修改白名单内，禁止修改: " + ", ".join(bad),
                "whitelist": list(perms.write_whitelist),
            }
        # 走到这里说明闸门已批准（gate 中间件在 handler 之前拦截）
        return {"approved": True, "paths": list(paths), "reason": reason}

    def vocab_check_plugin(path: str) -> Any:
        """在沙箱里跑一遍词插件的正反例（只读，不写盘、不加载）。

        path 为相对工作区根的 .py 文件路径，通常是 vocab_ext/proposals/ 下的草稿。
        申请把它放进会被执行的目录之前先跑这个，并把结果写进申请理由——批准的人
        要看得到正反例过没过。"""
        rel = str(path).strip()
        if not rel.endswith(_PLUGIN_SUFFIX) or ".." in rel:
            return {"ok": False, "error": f"只接受工作区内的 .py 文件: {rel}"}
        api_dir = ctx.workspace / "learning-buddy-api"
        target = (ctx.workspace / rel).resolve()
        try:
            target.relative_to(api_dir.resolve())
        except ValueError:
            return {"ok": False, "error": "插件文件必须在 learning-buddy-api/ 下"}
        if not target.is_file():
            return {"ok": False, "error": f"文件不存在: {rel}"}
        return _run_plugin_check(api_dir, target)

    def plugin_preflight(argmap: dict[str, Any]) -> dict[str, Any]:
        """闸门前自检：把每个待批插件的正反例结果并进批准卡，避免盲签。

        待批路径通常还不存在（草稿在 proposals/ 里），此时按同名约定回退到
        ``vocab_ext/proposals/<同名>.py``——所以起草时请保持文件名一致。
        """
        api_dir = ctx.workspace / "learning-buddy-api"
        out: dict[str, Any] = {}
        for raw in argmap.get("paths") or []:
            rel = str(raw).strip().replace("\\", "/")
            gate = gated_prefix(rel, gated)
            if not gate:
                continue
            name = rel[len(gate):]
            candidates = [rel, gate + _PROPOSALS + name]
            src = next(
                (c for c in candidates if (ctx.workspace / c).is_file()), None
            )
            if src is None:
                out[f"正反例自检 · {name}"] = "找不到文件（连 proposals/ 下的同名草稿也没有）"
                continue
            res = _run_plugin_check(api_dir, (ctx.workspace / src).resolve())
            verdict = "全绿" if res.get("ok") else "未通过"
            out[f"正反例自检 · {name}"] = {
                "自检对象": src,
                "结论": verdict,
                **{k: v for k, v in res.items() if k != "ok"},
            }
        return out

    def vocab_request_plugin_edit(paths: list[str], reason: str) -> Any:
        """申请把词插件放进会被执行的目录 configs/checks/vocab_ext/（专属闸：需用户批准）。

        与 fs.request_edit 分开的理由：那个目录里的 .py 会在 buddy-worker 进程里执行，
        不是声明文件。paths 必须是该目录**顶层**的具体 .py 文件（不接受目录、不接受
        proposals/ 下的草稿路径）。reason 里必须带上 vocab.check_plugin 的正反例结果。"""
        errs: list[str] = []
        for raw in paths:
            rel = str(raw).strip().replace("\\", "/")
            gate = gated_prefix(rel, gated)
            if not gate:
                errs.append(f"{rel}: 不在词插件目录内，普通文件请用 fs.request_edit")
                continue
            tail = rel[len(gate):]
            if not tail.endswith(_PLUGIN_SUFFIX):
                errs.append(f"{rel}: 只能申请 .py 插件文件")
            elif "/" in tail:
                errs.append(f"{rel}: 只能申请该目录顶层的文件，不接受子目录")
        if errs:
            return {"approved": False, "error": "; ".join(errs)}
        if not str(reason).strip():
            return {
                "approved": False,
                "error": "reason 不能为空：请附上 vocab.check_plugin 的正反例结果",
            }
        # 走到这里说明专属闸已批准（gate 中间件在 handler 之前拦截）
        return {"approved": True, "paths": list(paths), "reason": reason}

    def docker_rebuild(verify_files: list[str] | None = None) -> Any:
        """重建并重启 buddy-api + buddy-worker 容器（闸门：需用户批准；分钟级）。
        verify_files 为改动过的宿主机相对路径列表，重建后逐一核对容器内一致性。"""
        res = rebuild_buddy_services(ctx.workspace)
        if not res.ok:
            return {"ok": False, "error": res.stderr[-1500:]}
        checks: dict[str, bool] = {}
        for f in verify_files or []:
            rel = str(f)
            cpath = "/app/" + rel.removeprefix("learning-buddy-api/")
            for container in ("buddy-api", "buddy-worker"):
                r = verify_file_in_container(ctx.workspace, container, cpath, rel)
                checks[f"{container}:{rel}"] = r.ok
        ok = not checks or all(checks.values())
        return {
            "ok": ok,
            "rebuilt": ["buddy-api", "buddy-worker"],
            "file_checks": checks,
            **({} if ok else {"error": "重建后容器内文件与宿主机不一致"}),
        }

    def minio_stat(minio_key: str) -> Any:
        """预检 MinIO 对象是否存在（导入前 fail-fast）。经 c2-gateway 查询，
        仅支持 corpus/imports/ 与 import35b/ 前缀。

        `checked=false` 表示**预检没跑成**（令牌/网关问题），不代表对象不存在，
        此时不要据此拦住导入。
        """
        endpoint = ctx.settings.gateway_base.rstrip("/") + "/api/v1/studio/import/object-stat"
        # /api/v1/studio/* 由网关 StudioAuthMiddleware 守着，不带令牌恒 401。
        headers = ctx.settings.studio_auth_headers()
        try:
            r = httpx.get(endpoint, params={"key": minio_key}, headers=headers, timeout=15)
        except httpx.HTTPError as e:
            return {"checked": False, "error": f"gateway 不可达: {e}"}
        if r.status_code in (401, 403):
            return {
                "checked": False,
                "error": "Studio 令牌无效或缺失，预检不可用；这不代表对象不存在，直接继续导入。",
            }
        if r.status_code >= 400:
            return {"checked": False, "error": r.text[:300]}
        try:
            return {"checked": True, **r.json()}
        except ValueError as e:
            return {"checked": False, "error": f"响应非 JSON: {e}"}

    def env_resource_map() -> Any:
        """返回资源地图 resources.yaml 全文（东西在哪、怎么访问、能不能改）。"""
        path = ctx.settings.agent_env_dir / "resources.yaml"
        return path.read_text(encoding="utf-8") if path.is_file() else "(resources.yaml 缺失)"

    return [
        ToolSpec("fs.request_edit", "申请修改文件", fs_request_edit),
        ToolSpec("vocab.check_plugin", "词插件草稿自检", vocab_check_plugin),
        ToolSpec(
            "vocab.request_plugin_edit",
            "申请放入词插件（会被执行）",
            vocab_request_plugin_edit,
            preflight=plugin_preflight,
        ),
        ToolSpec("docker.rebuild", "重建 buddy 容器", docker_rebuild),
        ToolSpec("minio.stat", "MinIO 对象预检", minio_stat),
        ToolSpec("env.resource_map", "资源地图", env_resource_map),
    ]
