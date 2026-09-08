"""Load agent-editable Python from workspace `tools/`, with image seed fallback.

Every TOC / figure rule lives in `tools/*.py` so the agent can edit it (see
doc/toc-code-ownership.md、doc/fig-code-ownership.md). The `app/` side only
holds thin shims that call `load("toc_lexicon")` / `load("fig_size")` etc.
One module instance per name, cached in `sys.modules`.

`require=` names the attributes the caller depends on. A workspace copy that
lost them (stale seed from an older image, or a broken agent edit) is skipped
in favour of the image seed instead of taking the process down.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from app.config import settings

logger = logging.getLogger(__name__)

_SEED = Path(__file__).resolve().parent.parent / "workspace" / "tools"
_lock = threading.Lock()


def tools_dirs() -> list[Path]:
    """Runtime workspace first, image seed as fallback."""
    out: list[Path] = []
    for path in (settings.tools_dir, _SEED):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved not in out:
            out.append(resolved)
    return out


def ensure_on_path() -> None:
    """Put runtime `tools/` ahead of the image seed, whatever was on the path before.

    先摘掉再按序插：光判断「不在 path 里就插 0 位」不够 —— 运行态目录若已被别人插过，
    seed 反而会插到它前面，普通 `import toc_lexicon` 就拿到镜像里的旧代码。
    """
    wanted = [str(path) for path in tools_dirs() if str(path)]
    for text in wanted:
        while text in sys.path:
            sys.path.remove(text)
    for text in reversed(wanted):
        sys.path.insert(0, text)


def candidates(name: str) -> list[Path]:
    return [base / f"{name}.py" for base in tools_dirs() if (base / f"{name}.py").is_file()]


def find(name: str) -> Path | None:
    found = candidates(name)
    return found[0] if found else None


def _complete(module: object, require: Iterable[str]) -> bool:
    return all(hasattr(module, attr) for attr in require)


def _exec(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load workspace module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def load(name: str, require: Iterable[str] = ()) -> ModuleType:
    """Import `tools/<name>.py` once. Raises ImportError when nothing usable exists."""
    ensure_on_path()
    want = tuple(require)
    with _lock:
        cached = sys.modules.get(name)
        if isinstance(cached, ModuleType) and getattr(cached, "__file__", "") and _complete(cached, want):
            return cached
        paths = candidates(name)
        if not paths:
            raise ImportError(f"workspace module not found: {name}.py")
        last: Exception | None = None
        for index, path in enumerate(paths):
            sys.modules.pop(name, None)
            try:
                module = _exec(name, path)
            except Exception as exc:  # noqa: BLE001 - try the seed next
                last = exc
                logger.warning("workspace module %s failed at %s", name, path, exc_info=True)
                continue
            if _complete(module, want):
                if index:
                    logger.warning(
                        "workspace module %s at %s is stale or broken; fell back to %s",
                        name,
                        paths[0],
                        path,
                    )
                return module
            last = ImportError(f"{path} missing {want}")
        sys.modules.pop(name, None)
        raise ImportError(f"cannot load workspace module {name}: {last}")
