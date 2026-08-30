from __future__ import annotations

from pathlib import Path


def clear_traces(root: str = "") -> dict[str, int | list[str]]:
    """Delete all files under traces/ in the workspace."""
    base = Path(root) if root else Path.cwd()
    traces_dir = base / "traces"
    if not traces_dir.is_dir():
        return {"deleted": 0, "files": []}
    deleted: list[str] = []
    for path in sorted(traces_dir.iterdir()):
        if path.is_file():
            path.unlink()
            deleted.append(path.name)
    return {"deleted": len(deleted), "files": deleted}
