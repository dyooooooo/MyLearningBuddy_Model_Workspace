"""改进点整理 / 完备路径拆分：经 buddy-api 硬闸读写，推理由 Cursor Agent 完成。"""

from __future__ import annotations

from typing import Any

from ..registry import ToolContext, ToolSpec, register


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    client = ctx.client

    def ip_list_for_triage(fingerprint_hash: str = "", limit: int = 2000) -> Any:
        """列出待整理改进点（默认尽量全量，上限见 API）。返回 total_remaining/has_more。"""
        fp = (fingerprint_hash or "").strip() or str(
            ctx.session_payload().get("fingerprint_hash") or ""
        )
        return client.ip_list_for_triage(
            fingerprint_hash=fp, limit=int(limit or 2000)
        )

    def ip_list_for_split(fingerprint_hash: str = "", limit: int = 2000) -> Any:
        """列出待拆分改进点（全部拆分入口用）。返回 total_remaining/has_more。"""
        fp = (fingerprint_hash or "").strip() or str(
            ctx.session_payload().get("fingerprint_hash") or ""
        )
        return client.ip_list_for_split(
            fingerprint_hash=fp, limit=int(limit or 2000)
        )

    def ip_conflict_hints(fingerprint_hash: str = "") -> Any:
        """规则对立 hint（不写库）：同页 + 相似摘要 + 对立动词。仅供参考。"""
        fp = (fingerprint_hash or "").strip() or str(
            ctx.session_payload().get("fingerprint_hash") or ""
        )
        if not fp:
            return {"ok": False, "error": "fingerprint_hash required"}
        return client.ip_conflict_hints(fingerprint_hash=fp)

    def ip_triage_apply(
        fingerprint_hash: str = "",
        ready_ids: list[str] | None = None,
        split_ids: list[str] | None = None,
        aggregate_groups: list[dict[str, Any]] | None = None,
    ) -> Any:
        """提交整理分类结果并写库。同条优先 split；漏网保持待整理。"""
        fp = (fingerprint_hash or "").strip() or str(
            ctx.session_payload().get("fingerprint_hash") or ""
        )
        return client.ip_triage_apply(
            {
                "fingerprint_hash": fp,
                "ready_ids": list(ready_ids or []),
                "split_ids": list(split_ids or []),
                "aggregate_groups": list(aggregate_groups or []),
            }
        )

    def ip_split_propose(
        ip_id: str,
        children: list[dict[str, Any]] | None = None,
    ) -> Any:
        """组装/探测拆分提案（不写库）。children 项含 summary、step_indices、after_indices。"""
        return client.ip_split_propose(ip_id, children=list(children or []))

    def ip_split_apply(ip_id: str, children: list[dict[str, Any]]) -> Any:
        """确认拆分写库：每子条须完备 procedure；after 互不重叠且覆盖全部。"""
        if not children or len(children) < 2:
            return {"ok": False, "error": "至少 2 条子条"}
        return client.ip_split_apply(ip_id, list(children))

    def ip_aggregate_resolve(
        keep_ids: list[str] | None = None,
        delete_ids: list[str] | None = None,
    ) -> Any:
        """聚合簇裁决：保留 keep_ids，purge delete_ids。"""
        return client.ip_conflict_resolve(
            keep_ids=list(keep_ids or []),
            delete_ids=list(delete_ids or []),
        )

    def ip_list_ready(fingerprint_hash: str = "", limit: int = 500) -> Any:
        """列出整理完成、可以抽象的改进点（prep_status=ready）。

        抽象轨的原料入口。返回里 `pattern_id` 为空的才是还没归过组的。
        """
        return client.ip_list(
            fingerprint_hash=fingerprint_hash,
            status="accepted",
            prep_status="ready",
            limit=int(limit),
        )

    def ip_set_pattern(
        ip_id: str,
        suspect_type: str = "",
        abstract_text: str = "",
        pattern_status: str = "draft",
    ) -> Any:
        """把一条改进点挂到模式上。给了 suspect_type 就按症状名归并。

        症状名是跨书对齐的锚点：两条措辞完全不同但症状相同的改进点，只有靠它
        才会进同一个桶。不给就退回按摘要文本哈希归并，那个很脆。
        """
        if not ip_id:
            return {"ok": False, "error": "ip_id 必填"}
        body: dict[str, Any] = {"pattern_status": pattern_status}
        if suspect_type:
            body["suspect_type"] = suspect_type.strip().upper()
        if abstract_text:
            body["abstract_text"] = abstract_text
        return client.ip_set_pattern(ip_id, body)

    return [
        ToolSpec("ip.list_ready", "列出可抽象的改进点", ip_list_ready),
        ToolSpec("ip.set_pattern", "把改进点挂到模式", ip_set_pattern),
        ToolSpec("ip.list_for_triage", "列出待整理改进点", ip_list_for_triage),
        ToolSpec("ip.list_for_split", "列出待拆分改进点", ip_list_for_split),
        ToolSpec("ip.conflict_hints", "对立规则提示", ip_conflict_hints),
        ToolSpec("ip.triage_apply", "应用整理分类", ip_triage_apply),
        ToolSpec("ip.split_propose", "拆分提案组装", ip_split_propose),
        ToolSpec("ip.split_apply", "确认拆分写库", ip_split_apply),
        ToolSpec("ip.aggregate_resolve", "聚合裁决", ip_aggregate_resolve),
    ]
