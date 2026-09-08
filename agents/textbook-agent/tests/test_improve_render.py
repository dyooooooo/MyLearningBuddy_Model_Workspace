from __future__ import annotations

from app.workspace import ensure_workspace
from app.workspace_module import load


def _tool(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    return load("improve_render")


DIAGRAM = (
    "![公式](/api/v1/kb/proxy/asset?key=kb%2Fpdfs%2Fx%2Fassets%2Fmineru%2Feq.jpg)\n"
    "\n"
    "<!-- eq C + O_{2} xlongequal 点燃 CO_{2} -->\n"
    "\n"
    "## 下一节\n"
)


def test_keeps_diagram_formula_image_beside_eq_comment(tmp_path, monkeypatch) -> None:
    ir = _tool(tmp_path, monkeypatch)
    out = ir._polish_page(DIAGRAM, "safe")
    assert "![公式]" in out
    assert "<!-- eq C + O_{2} xlongequal 点燃 CO_{2} -->" in out
    assert r"--\gt" not in out
    assert "formula_shot" not in ir._scan_page("22", DIAGRAM)


def test_still_drops_true_formula_screenshot(tmp_path, monkeypatch) -> None:
    ir = _tool(tmp_path, monkeypatch)
    src = "![插图](/x.jpg)\n\nH_{2}O + O_{2}\n"
    out = ir._polish_page(src, "safe")
    assert "![插图]" not in out
    assert "H_{2}O" in out


def test_wrap_does_not_mutate_html_comments(tmp_path, monkeypatch) -> None:
    ir = _tool(tmp_path, monkeypatch)
    src = "正文\n\n<!-- eq C + O_{2} -->\n"
    assert ir._wrap_bare_math(src) == src
