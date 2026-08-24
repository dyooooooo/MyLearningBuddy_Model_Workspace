"""词插件自检：把一个 ``vocab_ext`` 插件文件跑一遍正反例，用 JSON 打出结果。

两处用它：

* **批准前**——buddy-copilot 的 Agent 在申请「把草稿移进会被执行的目录」之前先跑
  这个，把结果写进申请理由。批准的人于是看得到正反例过没过，不是盲签。
* **命令行**——``python -m app.services.vocab_plugin_check configs/checks/vocab_ext/x.py``

注意这只是**给人看的证据**。硬保证在加载器那一侧：正反例跑不过的插件根本不会
加载，原因出现在 ``GET /api/v1/admin/corpus/checks`` 的 ``load_errors`` 里。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.services.corpus_symptoms import (
    CHECKS_DIR,
    _word_example_errors,
    _words_from_plugins,
    load_lexicons,
    load_words,
    symptom_expr_functions,
)
from app.services.vocab_plugins import load_plugin_file


def check_plugin_file(path: Path, directory: Path | None = None) -> dict[str, Any]:
    """按当前生效的词表数据检查一个插件文件。不写盘、不改任何状态。"""
    root = directory or CHECKS_DIR
    lexicons = load_lexicons(root)
    plugin, load_errs = load_plugin_file(path, symptom_expr_functions(lexicons))
    if plugin is None:
        return {"ok": False, "file": str(path), "errors": load_errs}

    word = _words_from_plugins((plugin,))[0]
    # 插件的 REQUIRES 可能写着某个配置词（例如 has_math），求值时得能解析到它。
    others = tuple(w for w in load_words(root) if w.name != word.name)
    errors = _word_example_errors(others + (word,), lexicons, only=[word.name])
    failed = errors.get(word.name, [])
    return {
        "ok": not failed,
        "file": str(path),
        "name": word.name,
        "scope": word.scope,
        "doc": word.doc,
        "requires": list(word.requires),
        "examples": {
            "positive": len(word.examples.positive),
            "negative": len(word.examples.negative),
        },
        "errors": failed,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("用法: python -m app.services.vocab_plugin_check <插件文件> [...]")
        return 2
    reports = [check_plugin_file(Path(a)) for a in args]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
