from __future__ import annotations

from types import SimpleNamespace

from app import models_catalog, sdk_runner


def test_group_of() -> None:
    assert models_catalog.group_of("default") == "auto"
    assert models_catalog.group_of("grok-4.6", "Cursor Grok 4.6") == "cursor"
    assert models_catalog.group_of("composer-2.5", "Composer 2.5") == "cursor"
    assert models_catalog.group_of("claude-opus-5", "Claude Opus 5") == "other"


def test_public_model_from_sdk_object() -> None:
    item = SimpleNamespace(
        id="grok-4.6",
        display_name="Cursor Grok 4.6",
        parameters=(
            SimpleNamespace(
                id="effort",
                display_name="Effort",
                values=(SimpleNamespace(value="xhigh", display_name="Extra High"),),
            ),
            SimpleNamespace(
                id="fast",
                display_name="Fast",
                values=(SimpleNamespace(value="true", display_name="Fast\u200b"),),
            ),
        ),
        variants=(
            SimpleNamespace(
                display_name="Cursor Grok 4.6",
                is_default=True,
                params=(
                    SimpleNamespace(id="effort", value="high"),
                    SimpleNamespace(id="fast", value="true"),
                ),
            ),
        ),
    )
    row = models_catalog.public_model(item)
    assert row["group"] == "cursor"
    assert row["parameters"][0]["values"][0]["display_name"] == "Extra High"
    assert row["parameters"][1]["values"][0]["display_name"] == "Fast"
    assert row["variants"][0]["params"][0] == {"id": "effort", "value": "high"}


def test_model_selection_maps_auto_and_params() -> None:
    sel = sdk_runner.model_selection("auto", [{"id": "fast", "value": "true"}])
    assert getattr(sel, "id", None) == "default"
    params = list(getattr(sel, "params", []) or [])
    assert params[0].id == "fast"
    assert params[0].value == "true"


def test_model_selection_defaults_to_grok_high_fast() -> None:
    sel = sdk_runner.model_selection("", None)
    assert getattr(sel, "id", None) == "grok-4.6"
    pairs = {p.id: p.value for p in (getattr(sel, "params", []) or [])}
    assert pairs.get("effort") == "high"
    assert pairs.get("fast") == "true"


def test_title_model_is_composer_fast() -> None:
    from app.config import title_model, title_model_params

    assert title_model() == "composer-2.5"
    assert title_model_params() == [{"id": "fast", "value": "true"}]
    sel = sdk_runner.model_selection(title_model(), title_model_params())
    assert getattr(sel, "id", None) == "composer-2.5"
    pairs = {p.id: p.value for p in (getattr(sel, "params", []) or [])}
    assert "effort" not in pairs
    assert pairs.get("fast") == "true"


def test_summarize_title_uses_composer(tmp_path, monkeypatch) -> None:
    import cursor_sdk
    from app.config import settings

    captured: dict = {}

    class FakeAgent:
        @staticmethod
        def prompt(_text, opts, client=None):
            captured["model"] = getattr(opts, "model", None)
            return SimpleNamespace(result="修目录")

    monkeypatch.setattr(settings, "cursor_api_key", "k")
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setattr(cursor_sdk, "Agent", FakeAgent)
    monkeypatch.setattr(sdk_runner.sdk_client, "ensure_client", lambda: None)

    title = sdk_runner.summarize_title("请把目录页整理一下")
    assert title == "修目录"
    model = captured.get("model")
    assert getattr(model, "id", None) == "composer-2.5"
    pairs = {p.id: p.value for p in (getattr(model, "params", []) or [])}
    assert "effort" not in pairs
