"""Tests for MinerU runtime settings (Studio UI storage)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.mineru_settings import MinerUSettingsUpdate
from app.services import mineru_settings as svc


@pytest.fixture
def mineru_settings_file(monkeypatch, tmp_path):
    monkeypatch.setattr(svc.settings, "buddy_data_dir", str(tmp_path))
    return tmp_path / "settings" / "mineru.json"


def test_get_defaults_without_file(mineru_settings_file):
    out = svc.get_mineru_settings_out()
    assert out.api_token_set is False
    assert out.settings.model_version == "vlm"
    assert out.settings.api_base_url == "https://mineru.net"


def test_save_and_return_token(mineru_settings_file):
    saved = svc.update_mineru_settings(
        MinerUSettingsUpdate(
            api_token="secret-token",
            model_version="vlm",
            language="ch",
        )
    )
    assert saved.api_token_set is True
    assert saved.api_token == "secret-token"

    raw = json.loads(mineru_settings_file.read_text(encoding="utf-8"))
    assert raw["api_token"] == "secret-token"

    reloaded = svc.get_mineru_settings_out()
    assert reloaded.api_token == "secret-token"

    # Empty token in update keeps existing
    kept = svc.update_mineru_settings(MinerUSettingsUpdate(api_token=""))
    assert kept.api_token_set is True
    assert kept.api_token == "secret-token"


def test_effective_config_from_file(mineru_settings_file):
    svc.update_mineru_settings(
        MinerUSettingsUpdate(
            api_token="abc",
            model_version="pipeline",
            is_ocr=True,
            timeout_s=900,
        )
    )
    cfg = svc.get_effective_mineru_config()
    assert cfg.api_token == "abc"
    assert cfg.model_version == "pipeline"
    assert cfg.is_ocr is True
    assert cfg.timeout_s == 900.0
