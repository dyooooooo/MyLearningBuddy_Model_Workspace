from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import importlib.util

_HOOKS = Path(__file__).resolve().parents[1] / "workspace" / ".cursor" / "hooks"
_PY = _HOOKS / "guard-redline.py"
_SH = _HOOKS / "guard-redline.sh"
_spec = importlib.util.spec_from_file_location("guard_redline", _PY)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
decide = _mod.decide
DENY = _mod.DENY
ALLOW = _mod.ALLOW
DENY_MANAGED = _mod.DENY_MANAGED
DENY_LAYERS = _mod.DENY_LAYERS


def _payload(tool_input: dict, **extra) -> str:
    data = {
        "tool_name": "Read",
        "tool_input": tool_input,
        "transcript_path": "/data/agent/.cursor/projects/data-agent/agent-transcripts/x.jsonl",
        "workspace_roots": ["/data/agent"],
        "hook_event_name": "preToolUse",
    }
    data.update(extra)
    return json.dumps(data)


def _grant(tmp_path: Path, path: str, *, expired: bool = False) -> Path:
    root = tmp_path / "memory" / "approvals" / "granted"
    root.mkdir(parents=True, exist_ok=True)
    req = "ab" * 16
    delta = timedelta(minutes=-1) if expired else timedelta(minutes=15)
    ticket = {
        "req_id": req,
        "path": path,
        "expires_at": (datetime.now(timezone.utc) + delta).isoformat(),
        "one_shot": True,
    }
    dest = root / f"{req}.json"
    dest.write_text(json.dumps(ticket), encoding="utf-8")
    return dest


def test_workspace_read_allowed_despite_transcript_path() -> None:
    raw = _payload(
        {"file_path": "/data/agent/textbooks/66fdd0558d97406bac2b7840c9547165/pages/0007.md"}
    )
    assert decide(raw) == ALLOW


def test_write_tool_with_app_in_contents_allowed() -> None:
    raw = _payload(
        {
            "path": "/data/agent/tools/build_toc.py",
            "contents": "do not import /app source",
        }
    )
    assert decide(raw) == ALLOW


def test_write_tools_py_denied_without_ticket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True)
    raw = _payload(
        {"path": "/data/agent/tools/build_toc.py", "contents": "x = 1\n"},
        tool_name="Write",
    )
    assert decide(raw) == DENY_MANAGED


def test_write_tools_py_allowed_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ticket = _grant(tmp_path, "tools/build_toc.py")
    raw = _payload(
        {"path": "/data/agent/tools/build_toc.py", "contents": "x = 1\n"},
        tool_name="Write",
    )
    assert decide(raw) == ALLOW
    assert not ticket.exists()
    raw2 = _payload(
        {"path": "/data/agent/tools/refine_page.py", "contents": "y = 2\n"},
        tool_name="Write",
    )
    assert decide(raw2) == DENY_MANAGED


def test_ticket_path_mismatch_denied(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _grant(tmp_path, "tools/other.py")
    raw = _payload(
        {"path": "/data/agent/tools/build_toc.py", "contents": "x = 1\n"},
        tool_name="Write",
    )
    assert decide(raw) == DENY_MANAGED


def test_expired_ticket_denied(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ticket = _grant(tmp_path, "tools/build_toc.py", expired=True)
    raw = _payload(
        {"path": "/data/agent/tools/build_toc.py", "contents": "x = 1\n"},
        tool_name="Write",
    )
    assert decide(raw) == DENY_MANAGED
    assert not ticket.exists()


def test_write_hook_script_denied_without_ticket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    raw = _payload({"name": "guard-redline.py", "content": "print(1)\n"}, tool_name="write_hook_script")
    assert decide(raw) == DENY_MANAGED


def test_write_surface_denied_without_ticket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    raw = _payload(
        {"slot": "page-body", "path": "prepare.mjs", "content": "export {}\n"},
        tool_name="write_surface",
    )
    assert decide(raw) == DENY_MANAGED


def test_write_hook_script_allowed_with_ticket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ticket = _grant(tmp_path, ".cursor/hooks/guard-redline.py")
    raw = _payload({"name": "guard-redline.py", "content": "print(1)\n"}, tool_name="write_hook_script")
    assert decide(raw) == ALLOW
    assert not ticket.exists()


def test_write_approvals_dir_denied(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    raw = _payload(
        {"path": "/data/agent/memory/approvals/pending/ab.json", "contents": "{}\n"},
        tool_name="Write",
    )
    assert decide(raw) == DENY


def test_write_tool_relative_py_needs_ticket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True)
    raw = _payload({"path": "scan_headers.py", "content": "def scan_headers():\n    return {}\n"}, tool_name="write_tool")
    assert decide(raw) == DENY_MANAGED


def test_app_source_denied() -> None:
    raw = _payload({"file_path": "/app/app/kb/toc.py"})
    assert decide(raw) == DENY


def test_projects_target_denied() -> None:
    raw = _payload(
        {"file_path": "/data/agent/.cursor/projects/data-agent/agent-transcripts/x.jsonl"}
    )
    assert decide(raw) == DENY


def test_shell_workspace_allowed() -> None:
    raw = json.dumps({"command": "python3 tools/build_toc.py --book-id demo", "cwd": "/data/agent"})
    assert decide(raw) == ALLOW


def test_shell_app_denied() -> None:
    raw = json.dumps({"command": "sed -n 1p /app/app/kb/toc.py", "cwd": "/data/agent"})
    assert decide(raw) == DENY


def test_shell_pythonpath_app_allowed() -> None:
    raw = json.dumps(
        {
            "command": "cd /data/agent && PYTHONPATH=/app python3 -c \"from tools.toc_extract import toc_extract\"",
            "cwd": "/data/agent",
        }
    )
    assert decide(raw) == ALLOW


def test_sh_fallback_is_deny() -> None:
    text = _SH.read_text(encoding="utf-8")
    assert '"permission":"deny"' in text
    assert '"permission":"allow"' not in text


def test_write_page_skin_html_denied() -> None:
    raw = _payload(
        {
            "path": "/data/agent/textbooks/demo/pages/0004.md",
            "contents": '<ol class="kb-page-toc kb-page-toc-list"><li>第一单元</li></ol>\n',
        },
        tool_name="Write",
    )
    assert decide(raw) == DENY_LAYERS


def test_write_page_fence_denied() -> None:
    raw = _payload(
        {
            "path": "/data/agent/textbooks/demo/pages/0004.md",
            "contents": "::: kb-page-toc\n目录\n第一单元\n:::\n",
        },
        tool_name="Write",
    )
    assert decide(raw) == DENY_LAYERS


def test_write_page_glued_name_denied() -> None:
    raw = _payload(
        {
            "path": "/data/agent/textbooks/demo/pages/0032.md",
            "contents": "![分液](/x.jpg) kb-center\n*分液*\n",
        },
        tool_name="Write",
    )
    assert decide(raw) == DENY_LAYERS


def test_write_page_remnant_denied() -> None:
    raw = _payload(
        {
            "path": "/data/agent/textbooks/demo/pages/0057.md",
            "contents": "（2）$n = 2$\n\n kb-center\n$V = 22.4$\n:::\n",
        },
        tool_name="Write",
    )
    assert decide(raw) == DENY_LAYERS


def test_write_mid_fence_allowed() -> None:
    raw = _payload(
        {
            "path": "/data/agent/textbooks/demo/mid/0004.md",
            "contents": "::: kb-page-toc\n目录\n第一单元\n:::\n",
        },
        tool_name="Write",
    )
    assert decide(raw) == ALLOW


def test_write_mid_skin_html_denied() -> None:
    raw = _payload(
        {
            "path": "/data/agent/textbooks/demo/mid/0004.md",
            "contents": '<nav class="kb-page-toc"><ol class="kb-page-toc-list"></ol></nav>\n',
        },
        tool_name="Write",
    )
    assert decide(raw) == DENY_LAYERS


def test_write_page_official_md_allowed() -> None:
    raw = _payload(
        {
            "path": "/data/agent/textbooks/demo/pages/0004.md",
            "contents": "目录\n\n第一单元\n1\n沁园春·长沙 / 毛泽东 9\n",
        },
        tool_name="Write",
    )
    assert decide(raw) == ALLOW


def test_sh_prints_deny_when_python_missing(tmp_path) -> None:
    dest = tmp_path / "guard-redline.sh"
    dest.write_text(_SH.read_text(encoding="utf-8"), encoding="utf-8")
    dest.chmod(0o755)
    result = subprocess.run(
        ["bash", str(dest)],
        input="{}",
        capture_output=True,
        text=True,
        check=False,
    )
    assert '"permission":"deny"' in result.stdout
