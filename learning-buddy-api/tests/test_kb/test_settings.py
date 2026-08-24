from __future__ import annotations

from pathlib import Path

from app.kb.settings import (
    KbMineruConfigError,
    KbMineruUpdate,
    TEXTBOOK_PRESET,
    get_runtime,
    require_export_config,
    update_settings,
)


def test_preset_and_reject_html(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.kb.settings.settings.buddy_data_dir", str(tmp_path))
    out = update_settings(KbMineruUpdate(apply_textbook_preset=True, api_token="tok-1"))
    assert out.settings.model_version == TEXTBOOK_PRESET["model_version"]
    assert out.settings.enable_formula is True
    assert out.api_token_set is True
    try:
        update_settings(KbMineruUpdate(model_version="MinerU-HTML"))
        raise AssertionError("expected reject")
    except KbMineruConfigError:
        pass
    cfg = get_runtime()
    assert cfg.model_version == "vlm"
    require_export_config()


def test_require_token(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.kb.settings.settings.buddy_data_dir", str(tmp_path))
    try:
        require_export_config()
        raise AssertionError("expected missing token")
    except KbMineruConfigError:
        pass
