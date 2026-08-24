"""抽象轨工具插件：把已确认的改进点归纳成可复用规律，并判定写到哪个生效面。

这里全部只做**记账**（写 Postgres，可回滚）。真正写盘由 `fs.request_edit` /
`editions.publish` 闸门完成 —— 工具不替用户绕过批准。
"""

from __future__ import annotations

from typing import Any

from ..registry import ToolContext, ToolSpec, register

# 与 learning-buddy-api 的 LANDING_TARGETS 闭集保持一致。
# 服务端会再校验一次，这里先挡住明显的笔误，省一个来回。
_LANDING_TARGETS = (
    "L1.layout_rule",
    "L1.config_pattern",
    "L1.theme_css",
    "L0.normalize",
    "L0.renderer",
    "L3.book_patch",
)


def _brief(pat: dict[str, Any]) -> dict[str, Any]:
    """列表视图只保留定路由需要的字段，避免把上下文塞爆。"""
    return {
        "id": pat.get("id"),
        "category": pat.get("category"),
        "members": pat.get("member_count"),
        "status": pat.get("status"),
        "landing_target": pat.get("landing_target") or "(未定)",
        "landed_ref": pat.get("landed_ref") or "",
        # 映射面：没有 suspect_type 就永远不可能在新书里被认出来。
        "suspect_type": pat.get("suspect_type") or "(未命名症状)",
        "detector_ref": pat.get("detector_ref") or "",
        "remediation_kind": pat.get("remediation_kind") or "(未定档)",
        "book_count": pat.get("book_count"),
        "abstract": str(pat.get("abstract_text") or "")[:160],
    }


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    client = ctx.client

    def patterns_list(
        category: str = "",
        status: str = "",
        landing_target: str = "",
        min_members: int = 0,
        limit: int = 100,
    ) -> Any:
        """列出抽象模式（可按类目/状态/落地目标/成员数过滤），附全局分布计数。"""
        out = client.patterns_list(
            category=category,
            status=status,
            landing_target=landing_target,
            min_members=int(min_members),
            limit=int(limit),
        )
        pats = out.get("patterns") or []
        return {
            "ok": True,
            "count": len(pats),
            "patterns": [_brief(p) for p in pats],
            "counts": out.get("counts") or {},
            "landing_targets": _LANDING_TARGETS,
        }

    def patterns_get(pattern_id: str) -> Any:
        """看一条模式的全文摘要、操作模板与全部成员（含成员页号 = 该模式的黄金集）。"""
        out = client.pattern_get(pattern_id)
        return {"ok": True, "pattern": out}

    def patterns_merge(pattern_id: str, merge_ids: list[str], note: str = "") -> Any:
        """把重复的模式并进 pattern_id（成员改指过来，被并的模式删除）。"""
        if not merge_ids:
            return {"ok": False, "error": "merge_ids 为空"}
        out = client.pattern_merge(pattern_id, list(merge_ids), note)
        return {
            "ok": True,
            "kept": _brief(out.get("pattern") or {}),
            "merged_ids": out.get("merged_ids") or [],
            "moved_members": out.get("moved_members"),
        }

    def patterns_split(
        pattern_id: str,
        member_ip_ids: list[str],
        abstract_text: str,
        category: str = "",
        note: str = "",
    ) -> Any:
        """把混在同一个桶里的另一条规律拆出去，成员挑出来另立一条模式。"""
        if not member_ip_ids:
            return {"ok": False, "error": "member_ip_ids 为空"}
        if not abstract_text.strip():
            return {"ok": False, "error": "拆出的新模式必须给判据式摘要"}
        out = client.pattern_split(
            pattern_id,
            list(member_ip_ids),
            abstract_text,
            category=category,
            note=note,
        )
        return {
            "ok": True,
            "source": _brief(out.get("source") or {}),
            "created": _brief(out.get("created") or {}),
        }

    def patterns_route(pattern_id: str, landing_target: str, note: str = "") -> Any:
        """定路由：判定这条规律最终写到哪个生效面。note 里写清判定理由。

        闭集取值见返回的 landing_targets。判 L1.layout_rule 前先确认它能写成
        「满足某某几何条件的块判进某个 zone」—— 规则语言只能表达 zone。
        """
        target = (landing_target or "").strip()
        if target not in _LANDING_TARGETS:
            return {
                "ok": False,
                "error": f"未知 landing_target: {target!r}",
                "landing_targets": _LANDING_TARGETS,
            }
        out = client.pattern_route(pattern_id, target, note)
        return {"ok": True, "pattern": _brief(out.get("pattern") or {})}

    def patterns_land(pattern_id: str, landed_ref: str, note: str = "") -> Any:
        """落地记账：确认规律已写入生效面。先跑完全书回归再调。

        landed_ref 要填具体产物（规则 id / config 点路径 / CSS 选择器 / 测试节点 id），
        填得含糊，以后就没法从文件反查是哪条规律要求的。
        """
        ref = (landed_ref or "").strip()
        if not ref:
            return {"ok": False, "error": "landed_ref 必填"}
        out = client.pattern_land(pattern_id, ref, note)
        return {"ok": True, "pattern": _brief(out.get("pattern") or {})}

    # ---- 检测器：让模式能在新书里被认出来 ----

    def checks_list() -> Any:
        """当前生效的检测器清单 + 可用词表（写 when 表达式前先看这个）。"""
        out = client.checks_list()
        return {
            "ok": True,
            "count": out.get("count"),
            "checks": out.get("checks") or [],
            "vocabulary": out.get("vocabulary") or {},
            "checks_dir": out.get("checks_dir"),
        }

    def checks_dry_run(
        job_id: str,
        suspect_type: str = "",
        yaml_text: str = "",
        pages: list[int] | None = None,
        limit: int = 40,
    ) -> Any:
        """在真实导入页上试跑检测器，看它到底命中什么。不落库、不改文件。

        写完 YAML 先用这个，别直接发布。yaml_text 可以传还没落盘的草稿。
        """
        if not job_id:
            return {"ok": False, "error": "job_id 必填：检测器必须在真实页上验证"}
        if not (suspect_type or yaml_text.strip()):
            return {"ok": False, "error": "给 suspect_type（已发布）或 yaml_text（草稿）"}
        out = client.check_dry_run(
            job_id,
            suspect_type=suspect_type,
            yaml_text=yaml_text,
            pages=list(pages or []),
            limit=int(limit),
        )
        return out

    def checks_run_examples(suspect_type: str = "", yaml_text: str = "") -> Any:
        """跑正反例，量召回率和误报率。

        没过就别定 rule 档 —— 能查出来不等于能修好，报错了更糟。
        """
        if not (suspect_type or yaml_text.strip()):
            return {"ok": False, "error": "给 suspect_type 或 yaml_text"}
        return client.check_run_examples(suspect_type=suspect_type, yaml_text=yaml_text)

    def checks_report_false_positive(
        suspect_type: str,
        fingerprint: str = "",
        job_id: str = "",
        page_no: int = 0,
        block_id: str = "",
        note: str = "",
    ) -> Any:
        """记一条「这不是问题」。同指纹下次降一档严重度，但仍会显示。"""
        if not suspect_type:
            return {"ok": False, "error": "suspect_type 必填"}
        return client.check_report_false_positive(
            suspect_type,
            fingerprint=fingerprint,
            job_id=job_id,
            page_no=int(page_no),
            block_id=block_id,
            note=note,
        )

    def patterns_bind_detector(
        pattern_id: str,
        suspect_type: str,
        detector_ref: str = "",
        remediation_kind: str = "",
        playbook_ref: str = "",
        landing_target: str = "",
    ) -> Any:
        """给模式绑上症状名、检测器声明和处置档位 —— 抽象的收尾一步。

        suspect_type 是跨书对齐的锚点，绑了才可能在新书里被认出来。
        remediation_kind 定 rule 还是 playbook：同指纹的修复动作完全一致才配叫
        rule，不一致就老实写 playbook 手册。
        """
        if not suspect_type:
            return {"ok": False, "error": "suspect_type 必填（全大写下划线）"}
        out = client.pattern_bind_detector(
            pattern_id,
            suspect_type,
            detector_ref=detector_ref,
            remediation_kind=remediation_kind,
            playbook_ref=playbook_ref,
            landing_target=landing_target,
        )
        return {"ok": True, "pattern": _brief(out.get("pattern") or {})}

    def patterns_promotion_check(pattern_id: str) -> Any:
        """playbook 够不够格升 rule。只出提议，规则文件仍需人工批准后落笔。"""
        return client.pattern_promotion_check(pattern_id)

    def job_symptoms(job_id: str) -> Any:
        """一次导入查出的命名症状，分「已知（有成功路径）」和「未知（需诊断）」。"""
        if not job_id:
            return {"ok": False, "error": "job_id 必填"}
        return client.job_symptoms(job_id)

    def refresh_job_symptoms(job_id: str) -> Any:
        """按当前中间表示重跑检测，覆盖导入时的症状快照。"""
        if not job_id:
            return {"ok": False, "error": "job_id 必填"}
        return client.refresh_job_symptoms(job_id)

    return [
        ToolSpec("patterns.list", "列出抽象模式", patterns_list),
        ToolSpec("patterns.get", "查看模式与成员", patterns_get),
        ToolSpec("patterns.merge", "合并重复模式", patterns_merge),
        ToolSpec("patterns.split", "拆分混杂模式", patterns_split),
        ToolSpec("patterns.route", "定落地目标", patterns_route),
        ToolSpec("patterns.land", "落地记账", patterns_land),
        ToolSpec("patterns.bind_detector", "绑症状名与检测器", patterns_bind_detector),
        ToolSpec("patterns.promotion_check", "晋级门自检", patterns_promotion_check),
        ToolSpec("checks.list", "看检测器清单与词表", checks_list),
        ToolSpec("checks.dry_run", "真实页上试跑检测器", checks_dry_run),
        ToolSpec("checks.run_examples", "跑正反例量召回与误报", checks_run_examples),
        ToolSpec("checks.report_false_positive", "记一条误报", checks_report_false_positive),
        ToolSpec("symptoms.for_job", "看一次导入的症状清单", job_symptoms),
        ToolSpec("symptoms.refresh", "按当前中间表示重跑症状检测", refresh_job_symptoms),
    ]
