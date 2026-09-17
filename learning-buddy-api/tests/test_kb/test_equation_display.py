from __future__ import annotations

from pathlib import Path
import importlib.util


def _mod():
    path = (
        Path(__file__).resolve().parents[3]
        / "agents"
        / "textbook-agent"
        / "workspace"
        / "tools"
        / "equation_display.py"
    )
    spec = importlib.util.spec_from_file_location("test_equation_display_mod", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_math_is_not_diagram() -> None:
    eq = _mod()
    assert eq.is_diagram_latex(r"A \cup B") is False
    assert eq.is_diagram_latex(r"\ce{H2O}") is False
    assert eq.is_diagram_latex(r"x^{2} + y^{2} = 1") is False


def test_annotated_reaction_is_diagram() -> None:
    eq = _mod()
    tex = r"\begin{array}{rl} \mathrm{C} + \mathrm{O}_{2} \xlongequal{\text{点燃}} \mathrm{CO}_{2} \end{array}"
    assert eq.is_diagram_latex(tex) is True
    assert eq.is_diagram_latex(r"\chemfig{C(-[:90]H)}") is True
    assert eq.is_diagram_latex(r"\begin{array}{c} 氧化 \\ A \end{array}") is True


def test_diagram_asset_name_is_stable() -> None:
    eq = _mod()
    assert eq.diagram_asset_name(22, (10.2, 140.4, 400.1, 180.0)) == "eq-p0022-10-140-400-180.jpg"
