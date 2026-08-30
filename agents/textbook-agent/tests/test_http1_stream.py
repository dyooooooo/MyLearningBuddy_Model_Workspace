"""Agent runs must not ride HTTP/2 inside a container.

The long-lived HTTP/2 bidirectional stream gets silently dropped by NAT / proxy
idle timeouts in Docker, which is what Cursor surfaces as "Connection stalled
repeatedly". ``local.useHttp1ForAgent`` is the documented switch; the Python
dataclass has no field for it, so the value has to survive the SDK's mapping
normalisation to reach the bridge. That is what these tests pin.
"""

from __future__ import annotations

import pytest

from app import sdk_runner
from app.config import settings


class _Local:
    """Stands in for ``LocalAgentOptions``, which rejects unknown kwargs."""

    def __init__(self, **kw: object) -> None:
        self.kw = kw


def test_http1_is_requested_by_default() -> None:
    assert settings.cursor_use_http1 is True


def test_mapping_form_carries_the_switch(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cursor_use_http1", True)
    out = sdk_runner._local_options(_Local, {"cwd": "/data/agent"})
    assert isinstance(out, dict)
    assert out["useHttp1ForAgent"] is True
    assert out["cwd"] == "/data/agent"


def test_switching_it_off_keeps_the_dataclass_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cursor_use_http1", False)
    out = sdk_runner._local_options(_Local, {"cwd": "/data/agent"})
    assert isinstance(out, _Local)
    assert "useHttp1ForAgent" not in out.kw


def test_the_switch_survives_the_sdk_wire_normaliser(monkeypatch) -> None:
    """The whole approach rests on unknown keys being passed through."""
    types = pytest.importorskip("cursor_sdk.types")
    monkeypatch.setattr(settings, "cursor_use_http1", True)
    local = sdk_runner._local_options(
        _Local, {"cwd": "/data/agent", "setting_sources": ["project"]}
    )
    wire = types._normalize_local_options(local)
    assert wire["useHttp1ForAgent"] is True
    # sanity: the normaliser really did run
    assert wire["cwd"] == ["/data/agent"]
    assert wire["settingSources"] == ["project"]
