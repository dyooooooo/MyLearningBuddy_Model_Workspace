"""清理脚本的前缀收敛：只能收到「单次导入自己的目录」，绝不能收到共享层级。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "purge_orphan_import_objects.py"
_spec = importlib.util.spec_from_file_location("purge_orphan_import_objects", _PATH)
purge = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(purge)

MINERU_DEPTH = 2  # corpus/imports/
B35_DEPTH = 1  # import35b/


def test_mineru_objects_collapse_to_the_import_folder() -> None:
    assert (
        purge.object_prefix("corpus/imports/job1/source.pdf", MINERU_DEPTH)
        == "corpus/imports/job1/"
    )
    assert (
        purge.object_prefix("corpus/imports/job1/assets/p1/fig.png", MINERU_DEPTH)
        == "corpus/imports/job1/"
    )


def test_staging_uploads_keep_their_extra_level() -> None:
    assert (
        purge.object_prefix("corpus/imports/staging/abc/book.pdf", MINERU_DEPTH)
        == "corpus/imports/staging/abc/"
    )
    assert (
        purge.object_prefix("corpus/imports/staging/abc/assets/fig.png", MINERU_DEPTH)
        == "corpus/imports/staging/abc/"
    )
    assert (
        purge.object_prefix("import35b/staging/abc/book.pdf", B35_DEPTH)
        == "import35b/staging/abc/"
    )


def test_b35_objects_collapse_one_level_shallower() -> None:
    assert (
        purge.object_prefix("import35b/job1/source.pdf", B35_DEPTH) == "import35b/job1/"
    )
    assert (
        purge.object_prefix("import35b/job1/assets/p1/fig.png", B35_DEPTH)
        == "import35b/job1/"
    )


def test_shared_levels_are_refused() -> None:
    """根前缀下面直接放的对象没有归属目录，宁可不删。"""
    assert purge.object_prefix("corpus/imports/stray.pdf", MINERU_DEPTH) == ""
    assert purge.object_prefix("import35b/stray.pdf", B35_DEPTH) == ""
    assert purge.object_prefix("corpus/imports/", MINERU_DEPTH) == ""
