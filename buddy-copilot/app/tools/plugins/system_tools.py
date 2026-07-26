"""系统工具插件：文件修改申请、Docker 重建、MinIO 预检、资源地图。"""

from __future__ import annotations

from typing import Any

import httpx

from ...permissions import load_permissions
from ..docker_ops import rebuild_buddy_services, verify_file_in_container
from ..fs_guard import is_whitelisted
from ..registry import ToolContext, ToolSpec, register


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    perms = load_permissions(ctx.settings.agent_env_dir)

    def fs_request_edit(paths: list[str], reason: str) -> Any:
        """申请修改工作区文件（闸门：需用户批准）。paths 为相对工作区根的文件路径列表，
        必须全部在白名单内；批准后你才可以直接编辑这些文件。"""
        bad = [
            p for p in paths
            if not is_whitelisted(str(p), perms.write_whitelist, perms.forbidden)
        ]
        if bad:
            return {
                "approved": False,
                "error": "以下路径不在文件修改白名单内，禁止修改: " + ", ".join(bad),
                "whitelist": list(perms.write_whitelist),
            }
        # 走到这里说明闸门已批准（gate 中间件在 handler 之前拦截）
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
        仅支持 corpus/imports/ 与 import35b/ 前缀。"""
        try:
            r = httpx.get(
                ctx.settings.gateway_base.rstrip("/") + "/api/v1/studio/import/object-stat",
                params={"key": minio_key},
                timeout=15,
            )
            if r.status_code >= 400:
                return {"exists": False, "error": r.text[:300]}
            return r.json()
        except httpx.HTTPError as e:
            return {"exists": False, "error": f"gateway 不可达: {e}"}

    def env_resource_map() -> Any:
        """返回资源地图 resources.yaml 全文（东西在哪、怎么访问、能不能改）。"""
        path = ctx.settings.agent_env_dir / "resources.yaml"
        return path.read_text(encoding="utf-8") if path.is_file() else "(resources.yaml 缺失)"

    return [
        ToolSpec("fs.request_edit", "申请修改文件", fs_request_edit),
        ToolSpec("docker.rebuild", "重建 buddy 容器", docker_rebuild),
        ToolSpec("minio.stat", "MinIO 对象预检", minio_stat),
        ToolSpec("env.resource_map", "资源地图", env_resource_map),
    ]
