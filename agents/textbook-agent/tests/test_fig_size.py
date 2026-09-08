from __future__ import annotations

import json

from app.workspace import ensure_workspace

BOOK = "figsizebook"


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    from app import fig_size

    fig_size.reset_for_tests()
    return fig_size


def _write_blocks(blocks: list[dict], *, book: str = BOOK) -> None:
    from app.library import book_dir

    mineru = book_dir(book) / "mineru"
    mineru.mkdir(parents=True, exist_ok=True)
    (mineru / "blocks.json").write_text(json.dumps(blocks), encoding="utf-8")


def _body(x0: float, x1: float, page: int = 0) -> dict:
    return {"type": "paragraph", "bbox": [x0, 100, x1, 140], "page_idx": page}


def _figure(name: str, x0: float, x1: float, page: int = 0, y0: float = 200, y1: float = 400) -> dict:
    return {
        "type": "image",
        "bbox": [x0, y0, x1, y1],
        "page_idx": page,
        "content": {"image_source": {"path": f"images/{name}"}},
    }


def test_implementation_lives_in_workspace(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app.workspace_module import find

    path = find("fig_size")
    assert path is not None
    assert path.name == "fig_size.py"
    assert "tools" in path.parts


def test_disabled_by_default(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.delenv("TEXTBOOK_FIG_SIZE_BOOKS", raising=False)
    _write_blocks([_body(100, 900), _figure("a.jpg", 100, 500)])
    assert fig_size.book_fig_widths(BOOK) == {}


def test_allowlist_gates_per_book(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "otherbook")
    _write_blocks([_body(100, 900), _figure("a.jpg", 100, 500)])
    assert fig_size.book_fig_widths(BOOK) == {}
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", f"otherbook,{BOOK}")
    fig_size.reset_for_tests()
    assert fig_size.page_fig_widths(BOOK, 1)


def test_wildcard_enables_all(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _figure("a.jpg", 100, 500)])
    assert fig_size.page_fig_widths(BOOK, 1)


def test_width_is_relative_to_body_column(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    # Body column spans 100..900 => 800 wide. A 400-wide figure is half of it.
    _write_blocks(
        [_body(100, 900), _body(100, 900), _body(100, 900), _figure("half.jpg", 100, 500)]
    )
    assert fig_size.page_fig_widths(BOOK, 1)["half.jpg"] == 50.0


def test_short_lines_do_not_shrink_the_body_column(tmp_path, monkeypatch) -> None:
    """p90, not the median: most prose blocks are short lines and headings."""
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    blocks = [_body(100, 300) for _ in range(9)] + [_body(100, 900)]
    blocks.append(_figure("half.jpg", 100, 500))
    _write_blocks(blocks)
    assert fig_size.page_fig_widths(BOOK, 1)["half.jpg"] == 50.0


def test_full_page_figure_clamps_to_100(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _figure("plate.jpg", 0, 1000)])
    assert fig_size.page_fig_widths(BOOK, 1)["plate.jpg"] == 100.0


def test_tiny_figure_clamps_to_floor(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _figure("glyph.jpg", 100, 108)])
    assert fig_size.page_fig_widths(BOOK, 1)["glyph.jpg"] == 4.0


def test_fraction_bboxes_are_scaled(tmp_path, monkeypatch) -> None:
    """Some MinerU builds emit 0-1 fractions instead of 0-1000."""
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks(
        [
            {"type": "paragraph", "bbox": [0.1, 0.1, 0.9, 0.14], "page_idx": 0},
            {
                "type": "image",
                "bbox": [0.1, 0.2, 0.5, 0.4],
                "page_idx": 0,
                "content": {"image_source": {"path": "images/half.jpg"}},
            },
        ]
    )
    assert fig_size.page_fig_widths(BOOK, 1)["half.jpg"] == 50.0


def test_repeated_asset_on_a_page_keeps_the_widest(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks(
        [_body(100, 900), _figure("dup.jpg", 100, 300), _figure("dup.jpg", 100, 500)]
    )
    assert fig_size.page_fig_widths(BOOK, 1)["dup.jpg"] == 50.0


def test_pages_are_one_based(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _figure("p3.jpg", 100, 500, page=2)])
    assert fig_size.page_fig_widths(BOOK, 3) == {"p3.jpg": 50.0}
    assert fig_size.page_fig_widths(BOOK, 1) == {}


def test_missing_blocks_file_is_not_an_error(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    assert fig_size.book_fig_widths("neverimported") == {}


def test_side_by_side_figures_form_a_row(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks(
        [
            _body(100, 900),
            _figure("a.jpg", 100, 280, y0=500, y1=700),
            _figure("b.jpg", 300, 480, y0=505, y1=698),
            _figure("c.jpg", 500, 680, y0=502, y1=695),
        ]
    )
    assert fig_size.page_fig_rows(BOOK, 1) == [["a.jpg", "b.jpg", "c.jpg"]]


def test_stacked_figures_are_not_a_row(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks(
        [
            _body(100, 900),
            _figure("top.jpg", 200, 600, y0=100, y1=300),
            _figure("bot.jpg", 210, 590, y0=320, y1=520),
        ]
    )
    assert fig_size.page_fig_rows(BOOK, 1) == []


def test_rows_follow_the_allowlist(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.delenv("TEXTBOOK_FIG_SIZE_BOOKS", raising=False)
    _write_blocks(
        [
            _body(100, 900),
            _figure("a.jpg", 100, 280, y0=500, y1=700),
            _figure("b.jpg", 300, 480, y0=500, y1=700),
        ]
    )
    assert fig_size.page_fig_rows(BOOK, 1) == []


def _equation(
    name: str,
    x0: float,
    x1: float,
    page: int = 0,
    y0: float = 200,
    y1: float = 260,
    latex: str = r"\ce{H2O}",
) -> dict:
    content: dict = {"math_content": latex}
    if name:
        content["image_source"] = {"path": f"images/{name}"}
    return {
        "type": "equation_interline",
        "bbox": [x0, y0, x1, y1],
        "page_idx": page,
        "content": content,
    }


_DIAGRAM_TEX = (
    r"\begin{array}{rl} \mathrm{C} + \mathrm{O}_{2} "
    r"\xlongequal{\text{点燃}} \mathrm{CO}_{2} \end{array}"
)


def test_diagram_equation_uses_same_body_column(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _equation("eq.jpg", 100, 500, latex=_DIAGRAM_TEX)])
    assert fig_size.page_fig_widths(BOOK, 1)["eq.jpg"] == 50.0


def test_diagram_equation_without_path_uses_generated_name(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _equation("", 100, 500, latex=_DIAGRAM_TEX)])
    name = "eq-p0001-100-200-500-260.jpg"
    assert fig_size.page_fig_widths(BOOK, 1)[name] == 50.0


def test_plain_equation_without_asset_is_not_sized(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _equation("", 100, 500, latex=r"H_2O")])
    assert fig_size.page_fig_widths(BOOK, 1) == {}


def test_cache_follows_blocks_mtime(tmp_path, monkeypatch) -> None:
    fig_size = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("TEXTBOOK_FIG_SIZE_BOOKS", "*")
    _write_blocks([_body(100, 900), _figure("a.jpg", 100, 500)])
    assert fig_size.page_fig_widths(BOOK, 1)["a.jpg"] == 50.0
    from app.library import book_dir

    path = book_dir(BOOK) / "mineru" / "blocks.json"
    path.write_text(
        json.dumps([_body(100, 900), _figure("a.jpg", 100, 300)]), encoding="utf-8"
    )
    # Size differs, so the stamp changes even where mtime resolution is coarse.
    assert fig_size.page_fig_widths(BOOK, 1)["a.jpg"] == 25.0
