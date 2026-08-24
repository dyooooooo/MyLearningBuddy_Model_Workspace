from __future__ import annotations

from pathlib import Path

from app.config import settings


class PathJailError(ValueError):
    """Path is outside the Agent workspace."""


def home() -> Path:
    return settings.home.resolve()


def is_inside_home(path: Path | str) -> bool:
    try:
        resolve_in_home(path)
        return True
    except PathJailError:
        return False


def resolve_in_home(raw: Path | str, *, base: Path | None = None) -> Path:
    root = home()
    text = str(raw or "").strip()
    if not text:
        raise PathJailError("empty path")
    p = Path(text)
    if not p.is_absolute():
        p = (base or root) / p
    resolved = p.resolve()
    if resolved != root and root not in resolved.parents:
        raise PathJailError(f"path outside workspace: {text}")
    return resolved
