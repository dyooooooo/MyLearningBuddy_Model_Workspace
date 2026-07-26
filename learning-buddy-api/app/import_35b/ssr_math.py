"""v4 编译面 SSR：公式在服务端一次性渲染成内联 SVG，page.html 自包含。

设计公理「渲染只发生一次」的落点：
- 编译时经 ``app/math/mathjax_batch.mjs``（一页一次 Node 调用 + 进程内缓存）
  把每个 ``data-latex`` 占位 span 替换成内联 SVG；
- 原 LaTeX 降级为 ``data-latex-src`` 元数据——共享的前端
  ``typesetTextbookMath`` 只认 ``data-latex``，天然跳过，35B 线从此
  不依赖任何客户端 MathJax 配置；
- 渲染失败输出**可见**的 ``math--error`` 标记（unicode 降级文本），
  绝不静默把原始 LaTeX 吐到页面上。

桥不可用时原样返回（保留 ``data-latex``，客户端仍可兜底渲染）。
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MATH_DIR = Path(__file__).resolve().parents[1] / "math"
_BATCH = _MATH_DIR / "mathjax_batch.mjs"

# 进程级公式缓存：教材页公式重复率高（如 $A$、$B$），命中即免渲染
_CACHE: dict[tuple[str, bool], dict[str, Any]] = {}

_SPAN_RE = re.compile(
    r'<span class="(math math--(?:inline|display)[^"]*)" data-latex="([^"]*)"></span>'
)


@lru_cache(maxsize=1)
def ssr_available() -> bool:
    node_modules = _MATH_DIR / "node_modules" / "mathjax-full"
    return _BATCH.is_file() and node_modules.is_dir()


def _render_batch(items: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    """(latex, display) 列表 → [{html, error}]，一次 Node 进程完成全部渲染。"""
    payload = json.dumps(
        {"items": [{"latex": lx, "display": dp} for lx, dp in items]},
        ensure_ascii=False,
    )
    try:
        proc = subprocess.run(
            ["node", str(_BATCH)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(_MATH_DIR),
            check=False,
        )
        data = json.loads(proc.stdout.strip() or "{}")
        results = data.get("results") or []
    except Exception as exc:
        logger.warning("mathjax batch SSR failed: %s", exc)
        results = []
    if len(results) != len(items):
        results = [{"html": "", "error": True} for _ in items]
    return results


def _error_span(cls: str, latex: str) -> str:
    from app.import_35b.page_compiler import _delatex  # lazy: 避免循环导入

    fallback = html_mod.escape(_delatex(latex)) or html_mod.escape(latex)
    return (
        f'<span class="{cls} math--error"'
        f' data-latex-src="{html_mod.escape(latex)}" title="公式渲染失败">{fallback}</span>'
    )


def ssr_typeset_page(html: str, *, report: list[dict[str, Any]] | None = None) -> str:
    """把编译产物里的 ``data-latex`` 占位 span 全部替换为内联 SVG。

    ``report``（可选）收集渲染失败记录，供校验面生成 finding。
    """
    if not html or "data-latex" not in html:
        return html
    if not ssr_available():
        if report is not None:
            report.append({"suspect_type": "SSR_UNAVAILABLE"})
        return html

    matches = list(_SPAN_RE.finditer(html))
    if not matches:
        return html

    jobs: list[tuple[str, bool]] = []
    keys: list[tuple[str, bool]] = []
    for m in matches:
        latex = html_mod.unescape(m.group(2))
        display = "math--display" in m.group(1)
        key = (latex, display)
        keys.append(key)
        if key not in _CACHE and key not in dict.fromkeys(jobs):
            jobs.append(key)

    if jobs:
        for key, result in zip(jobs, _render_batch(jobs)):
            _CACHE[key] = result

    def _sub(m: re.Match[str]) -> str:
        cls = m.group(1)
        latex = html_mod.unescape(m.group(2))
        display = "math--display" in cls
        result = _CACHE.get((latex, display)) or {"html": "", "error": True}
        if result.get("error") or not result.get("html"):
            if report is not None:
                report.append(
                    {"suspect_type": "MATH_RENDER_FAILED", "latex": latex, "display": display}
                )
            return _error_span(cls, latex)
        return (
            f'<span class="{cls} math--ssr"'
            f' data-latex-src="{html_mod.escape(latex)}">{result["html"]}</span>'
        )

    return _SPAN_RE.sub(_sub, html)
