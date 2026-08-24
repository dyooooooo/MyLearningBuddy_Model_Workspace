"""buddy-copilot 离线基础测试（无需 Cursor API key / GPU）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent_runner import extract_final_report  # noqa: E402
from app.permissions import load_permissions  # noqa: E402
from app.schemas import FinalReport, find_placeholders  # noqa: E402
from app.tools.fs_guard import WorktreeSnapshot, is_auto_allowed, is_whitelisted  # noqa: E402

ENV_DIR = Path(__file__).resolve().parent.parent / "agent-env"


# ---- fs_guard ----


def test_rollback_whitelist_per_file(tmp_path):
    """按文件回滚：改的恢复、新增的删掉、没动的不碰（不整树 rmtree）。"""
    d = tmp_path / "learning-buddy-api" / "configs" / "editions"
    d.mkdir(parents=True)
    keep = d / "keep.json"
    keep.write_text("same")
    mod = d / "mod.json"
    mod.write_text("v1")
    keep_mtime = keep.stat().st_mtime_ns

    snap = WorktreeSnapshot(tmp_path)
    snap.backup_whitelist()
    mod.write_text("v2")
    new = d / "new.json"
    new.write_text("x")

    restored, failed = snap.rollback_whitelist()
    assert failed == []
    assert mod.read_text() == "v1"
    assert not new.exists()
    # 未改动的文件不被触碰（整树 rmtree 会重建它）
    assert keep.stat().st_mtime_ns == keep_mtime
    snap.cleanup()


def test_rollback_violations_restores_untracked_source(tmp_path):
    """越权改动要真回滚：guarded_roots 里的源码即使从未入 git 也能字节级还原。"""
    app_dir = tmp_path / "learning-buddy-api" / "app" / "services"
    app_dir.mkdir(parents=True)
    src = app_dir / "document_ir.py"
    src.write_text("ORIGINAL\n")
    asset = app_dir / "big.png"  # 非源码后缀：不备份，不参与回滚
    asset.write_bytes(b"\x89PNG")

    snap = WorktreeSnapshot(tmp_path)
    snap.backup_guarded()
    src.write_text("AGENT EDIT\n")
    added = app_dir / "sneaky.py"
    added.write_text("print('x')\n")

    restored, failed = snap.rollback_all_violations(
        [
            "learning-buddy-api/app/services/document_ir.py",
            "learning-buddy-api/app/services/sneaky.py",
        ]
    )
    assert failed == []
    assert src.read_text() == "ORIGINAL\n"
    assert not added.exists()
    assert asset.read_bytes() == b"\x89PNG"
    assert len(restored) == 2
    snap.cleanup()


def test_rollback_violations_reports_unbacked_files(tmp_path):
    """备份范围外的文件如实报「未回滚」，不谎报成功。"""
    other = tmp_path / "doc"
    other.mkdir()
    f = other / "notes.md"
    f.write_text("hi")

    snap = WorktreeSnapshot(tmp_path)
    snap.backup_guarded()
    restored, failed = snap.rollback_all_violations(["doc/notes.md"])

    assert restored == []
    assert failed and "未回滚" in failed[0]
    assert f.read_text() == "hi"
    snap.cleanup()


def test_whitelist():
    assert is_whitelisted("learning-buddy-api/configs/editions/foo/config.json")
    assert is_whitelisted("learning-buddy-api/data/prompts/mineru_page_refine_v2.md")
    assert is_whitelisted("learning-buddy-api/app/import_35b/prompts/layout_blueprint_v6.md")
    assert is_whitelisted("learning-buddy-api/tests/test_column_detect.py")
    assert is_whitelisted("learning-buddy-api/app/static/textbook-hujiao.css")
    assert not is_whitelisted("learning-buddy-api/app/services/document_ir.py")
    assert not is_whitelisted("learning-buddy-c2/docker-compose.yml")
    assert not is_whitelisted("learning-buddy-api/configs/editions/x/.env")
    assert not is_whitelisted("exam-ocr-server/app/main.py")


def test_auto_allow_display_assets():
    """白名单内 CSS/HTML 免闸门；同目录 .json / 白名单外 .css 不免。"""
    assert is_auto_allowed(
        "learning-buddy-api/configs/editions/hujiao_math_hs_v1/assets/theme.css"
    )
    assert is_auto_allowed("learning-buddy-api/app/static/textbook-hujiao.css")
    assert not is_auto_allowed(
        "learning-buddy-api/configs/editions/hujiao_math_hs_v1/config.json"
    )
    assert not is_auto_allowed("learning-buddy-api/app/services/document_ir.py")
    assert not is_auto_allowed("exam-ocr-server/app/static/foo.css")


def test_whitelist_from_permissions_yaml():
    """fs_guard 白名单可由 permissions.yaml 注入（数据驱动）。"""
    perms = load_permissions(ENV_DIR)
    assert is_whitelisted(
        "learning-buddy-api/configs/editions/a/config.json",
        perms.write_whitelist,
        perms.forbidden,
    )
    assert is_whitelisted(
        "learning-buddy-api/app/static/textbook-hujiao.css",
        perms.write_whitelist,
        perms.forbidden,
    )
    assert is_auto_allowed(
        "learning-buddy-api/app/static/textbook-hujiao.css",
        perms.write_whitelist,
        perms.forbidden,
        perms.auto_allow_suffixes,
    )
    assert ".css" in perms.auto_allow_suffixes
    assert not is_whitelisted("learning-buddy-c2/.env", perms.write_whitelist, perms.forbidden)


# ---- 最终报告 ----


def test_extract_final_report_valid():
    text = """执行完毕。
```json
{"status": "done", "summary": "已复用方案 x 完成导入", "evidence": ["golden 通过", "page_diff 0.98"]}
```"""
    r = extract_final_report(text)
    assert r is not None and r.status == "done"
    assert len(r.evidence) == 2


def test_extract_final_report_blocked_with_question():
    text = '```json\n{"status": "blocked", "summary": "s", "question": "选 A 还是 B？"}\n```'
    r = extract_final_report(text)
    assert r is not None and r.status == "blocked" and r.question


def test_extract_final_report_none_cases():
    assert extract_final_report("我还在处理……") is None
    # JSON 坏 / 不符合 schema → None（监督器会追问）
    assert extract_final_report('```json\n{"status": broken}\n```') is None
    assert extract_final_report('```json\n{"status": "flying", "summary": "x"}\n```') is None


def test_final_report_schema():
    r = FinalReport(status="failed", summary="s")
    assert r.evidence == [] and r.question == ""


# ---- 占位符校验 ----


def test_find_placeholders_hits():
    args = {
        "minio_key": "<待上传后填入>",
        "job_id": "{job_id}",
        "note": "这里 TODO 补充",
        "nested": {"k": ["<x>"]},
    }
    hits = find_placeholders(args)
    assert len(hits) == 4
    assert any(h.startswith("minio_key=") for h in hits)
    assert any("nested.k[0]" in h for h in hits)


def test_find_placeholders_clean():
    args = {
        "minio_key": "corpus/imports/staging/ab12/x.pdf",
        "page_no": 3,
        "html": "<div>正文里的 HTML 标签不受影响</div>",
        "patch_json": '{"config.geometry.aside_x": 0.7}',
    }
    assert find_placeholders(args) == []


# ---- permissions.yaml ----


def test_permissions_tiers_and_gates():
    perms = load_permissions(ENV_DIR)
    gated = {n for n, p in perms.tools.items() if p.gated}
    assert gated == {
        "editions.publish",
        "fs.request_edit",
        "docker.rebuild",
        # 词插件专属闸：批的是会在 buddy-worker 里执行的受限 Python，
        # 与 fs.request_edit 分开，批了 configs/checks/ 不覆盖它
        "vocab.request_plugin_edit",
    }
    assert perms.tool("editions.overview").tier == "read"
    assert perms.tool("corpus.start_import").tier == "run"
    assert perms.tool("editions.save_draft").tier == "draft"
    assert perms.tier_auto("read") and perms.tier_auto("run") and perms.tier_auto("draft")
    assert not perms.tier_auto("gated")
    with pytest.raises(KeyError):
        perms.tool("shell.exec")


def test_permissions_scenario_visibility():
    """接口隔离：import_advice 可申请改文件但不能重建容器；无 page_assist 场景。"""
    perms = load_permissions(ENV_DIR)
    assert "page_assist" not in perms.scenario_tools
    imp = set(perms.visible_tools("import_advice"))
    # 碰到代码级阻塞要有合法通道（否则会直接动源码），但重建仍走人工
    assert "fs.request_edit" in imp and "docker.rebuild" not in imp
    assert "corpus.start_import" in imp and "editions.publish" in imp
    assert "symptoms.refresh" in imp and "symptoms.for_job" in imp
    # 全书一次导入：分段 extend 已下线
    assert "corpus.extend_import" not in perms.tools
    assert "corpus.extend_import" not in imp
    # 未声明场景 → 全集
    assert set(perms.visible_tools("")) == set(perms.tools)


def test_pattern_abstract_scenario_is_abstraction_only():
    """抽象轨只做归纳与落地：给落地通道，但不给改单页 IR 的工具。"""
    perms = load_permissions(ENV_DIR)
    pa = set(perms.visible_tools("pattern_abstract"))
    assert {"patterns.list", "patterns.route", "patterns.land"} <= pa
    # 落地要走批准闸门，且 config/CSS 类要能发布
    assert "fs.request_edit" in pa and "editions.publish" in pa
    # 验证成员页黄金集需要全书重跑与前后对比
    assert "corpus.rebuild_ir" in pa and "harness.page_diff" in pa
    # 这个场景不修单页
    assert not ({"corpus.patch_block", "corpus.replace_page_blocks"} & pa)
    # run_golden 对 MinerU 线是假验证，不该出现在抽象轨的工具面
    assert "harness.run_golden" not in pa


def test_registered_scenarios_have_permissions_and_skills():
    """新场景要三处齐活：Scenario 注册、permissions 工具面、SKILL_BY_KIND 技能。"""
    from app.agent_env import SKILL_BY_KIND
    from app.scenarios import SCENARIOS

    perms = load_permissions(ENV_DIR)
    for kind in SCENARIOS:
        assert kind in perms.scenario_tools, f"{kind} 没有 permissions 工具面"
        assert SKILL_BY_KIND.get(kind), f"{kind} 没有技能文件"
        for name in SKILL_BY_KIND[kind]:
            assert (ENV_DIR / "skills" / name).is_file(), f"{kind} 的技能 {name} 不存在"


def test_every_scenario_tool_has_a_tier():
    """场景里写了却没声明档位的工具会在运行时才炸，这里提前拦住。"""
    perms = load_permissions(ENV_DIR)
    for scenario, tools in perms.scenario_tools.items():
        unknown = [t for t in tools if t not in perms.tools]
        assert not unknown, f"{scenario} 引用了未声明档位的工具: {unknown}"


def test_permissions_budgets():
    b = load_permissions(ENV_DIR).budgets
    assert b.run_timeout_s > 0 and b.gate_timeout_s > 0
    assert b.max_tool_calls > 0 and b.max_nudges >= 1


# ---- store ----


def test_store_roundtrip(tmp_path):
    from app.store import Store

    st = Store(tmp_path / "t.db")
    sid = st.create_session("import_advice", {"job_id": "j", "page_no": 3})
    st.add_message(sid, "user", {"text": "hi"})
    st.add_message(sid, "assistant", {"text": "yo"})
    msgs = st.get_messages(sid, after_seq=0)
    assert [m["seq"] for m in msgs] == [1, 2]
    assert st.get_messages(sid, after_seq=1)[0]["role"] == "assistant"

    aid = st.create_approval(sid, "fs.request_edit", {"tool": "fs.request_edit"})
    assert st.decide_approval(aid, False, note="先别动文件")
    assert not st.decide_approval(aid, True)  # 已处理不可重复决策
    a = st.get_approval(aid)
    assert a["status"] == "rejected" and a["note"] == "先别动文件"
    assert st.list_approvals(sid, "fs.request_edit")[0]["id"] == aid
    assert st.list_approvals(sid, "docker.rebuild") == []

    # 会话级「总是允许」（remember）
    assert not st.has_session_allow(sid, "editions.publish")
    aid2 = st.create_approval(sid, "editions.publish", {"tool": "editions.publish"})
    assert st.decide_approval(aid2, True, remember=True)
    assert st.has_session_allow(sid, "editions.publish")
    assert not st.has_session_allow(sid, "fs.request_edit")  # 拒绝过的不算
    sid2 = st.create_session("import_advice", {})
    assert not st.has_session_allow(sid2, "editions.publish")  # 不跨会话

    st.update_session(sid, status="done", plan={"status": "done", "summary": "s"})
    s = st.get_session(sid)
    assert s["status"] == "done" and s["plan"]["status"] == "done"

    st.audit(sid, "user", "x", {"k": 1})
    assert st.get_audit(sid)[0]["action"] == "x"


def test_parse_pages_expressions():
    from app.tools.plugins.preview_tools import _parse_pages

    assert _parse_pages("1-4", 100) == [1, 2, 3, 4]
    assert _parse_pages("1,3,5-7", 100) == [1, 3, 5, 6, 7]
    assert _parse_pages("all", 5) == [1, 2, 3, 4, 5]
    assert _parse_pages("90-120", 100) == list(range(90, 101))
    assert len(_parse_pages("all", 500)) == 12  # 单次上限（防桥接冲垮）


def test_final_report_page_plan():
    from app.agent_runner import extract_final_report

    text = """收尾。
```json
{"status": "done", "summary": "页级方案已定",
 "page_plan": [
   {"pages": "1-2", "category": "目录", "edition_id": "hujiao_math_hs_v1", "action": "mineru"},
   {"pages": "3-12", "category": "正文双栏+侧栏", "edition_id": "hujiao_math_hs_v1",
    "action": "reprocess:layout_content", "notes": "侧栏例题需复核"}
 ]}
```"""
    r = extract_final_report(text)
    assert r is not None and len(r.page_plan) == 2
    assert r.page_plan[0].pages == "1-2" and r.page_plan[1].action.startswith("reprocess")


def test_context_pack_contains_all_sections():
    from app.agent_env import build_context_pack
    from app.config import get_settings

    s = get_settings()
    pack = build_context_pack(s.agent_env_dir, "import_advice", "TASK-XYZ")
    for marker in ("最终报告", "资源地图", "TASK-XYZ", "fs.request_edit"):
        assert marker in pack, f"missing {marker}"
    assert "page-diagnosis" in pack or "坏页" in pack


def test_classify_brief_message_intents():
    from app.brief_intent import classify_brief_message

    assert classify_brief_message("请告诉我你接下来要执行哪些动作？") == "clarify"
    assert classify_brief_message("接下来做什么") == "clarify"
    assert classify_brief_message("确认执行") == "approve"
    assert classify_brief_message("开始执行") == "approve"
    assert classify_brief_message("拒绝，改成用另一套方案") == "revise"
    assert classify_brief_message("打回，重新规划") == "revise"
    assert classify_brief_message("好的了解了") == "clarify"  # 默认不打回


def test_wall_elapsed_pauses_while_gate_pending():
    from app.loop import wall_elapsed

    started = 100.0
    # 跑了 50s 后开始等批准，又等了 800s
    assert wall_elapsed(started, now=150.0) == 50.0
    assert wall_elapsed(started, now=950.0, pause_start=150.0) == 50.0
    # 批准后累计暂停 800s，再跑 10s
    assert wall_elapsed(started, now=960.0, paused=800.0) == 60.0


def test_grant_write_scope_covers_forked_edition(tmp_path):
    from app.config import Settings
    from app.loop import LoopEngine
    from app.store import Store
    from app.tools.fs_guard import covered_by_approval
    from app.tools.plugins.editions_tools import edition_dir_rel

    settings = Settings(copilot_db_path=tmp_path / "c.db")
    st = Store(settings.db_path)
    sid = st.create_session("import_advice", {})
    rel = edition_dir_rel("hujiao_math_hs_v1")
    st.grant_write_scope(sid, [rel], reason="finalize")
    rows = st.list_approvals(sid, "tool_write_scope")
    assert rows and rows[0]["status"] == "approved"
    assert rows[0]["action"]["scope_paths"] == [rel]
    assert covered_by_approval(rel + "config.json", [rel])

    engine = LoopEngine(settings, st, lambda kind, payload: "")
    assert rel in engine._approved_edit_paths(sid)


def test_render_preview_sends_studio_token(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from app.config import Settings
    from app.tools.plugins import preview_tools

    captured: dict[str, object] = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers or {}
        request = httpx.Request("GET", url)
        response = httpx.Response(401, request=request, text="need token")
        raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(preview_tools.httpx, "get", fake_get)
    settings = Settings(studio_token="tok-preview", gateway_base="http://127.0.0.1:8000")
    ctx = SimpleNamespace(settings=settings)
    specs = {s.name: s for s in preview_tools.build(ctx)}
    out = specs["corpus.render_preview"].fn("corpus/imports/staging/x.pdf", pages="1-1")
    assert captured["headers"].get("Authorization") == "Bearer tok-preview"
    assert out.get("checked") is False
    assert "401" in str(out.get("error") or "") or "令牌" in str(out.get("error") or "")
