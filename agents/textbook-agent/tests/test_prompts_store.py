from __future__ import annotations

from app.config import settings
from app import prompts_store
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    ensure_workspace()
    return home


def test_missing_or_empty_falls_back_to_seed(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    path = home / "prompts" / "chat.md"
    assert path.is_file()
    assert prompts_store.get("chat")
    path.write_text("", encoding="utf-8")
    assert prompts_store.get("chat") == prompts_store.SEEDS["chat"]
    path.unlink()
    assert prompts_store.get("chat") == prompts_store.SEEDS["chat"]


def test_put_and_delete_prompt(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = prompts_store.put_prompt("chat", "你是测试提示词。\n用户：\n")
    assert "测试提示词" in row["content"]
    assert prompts_store.get("chat").startswith("你是测试")
    assert prompts_store.delete_prompt("chat")
    assert prompts_store.get("chat") == prompts_store.SEEDS["chat"]


def test_title_template_always_seed(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    prompts_store.put_prompt("title", "坏掉的标题提示 {blob}")
    assert prompts_store.title_template() == prompts_store.SEEDS["title"]
    assert "不超过16个汉字" in prompts_store.title_template()


def test_unknown_prompt_rejected(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    try:
        prompts_store.put_prompt("nope", "x")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
