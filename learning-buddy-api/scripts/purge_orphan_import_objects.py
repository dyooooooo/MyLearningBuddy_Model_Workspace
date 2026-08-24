#!/usr/bin/env python3
"""清理 MinIO 里没有任务认领的导入对象（孤儿对象）。

两条导入线各算各的：MinerU 线只看 ``corpus/imports/``，35B 线只看 ``import35b/``，
各自的「在用前缀」由各自的任务表推出，互不读写对方的资源。

默认只报告不删；确认无误再加 ``--apply``。刚上传还没建任务的 PDF 也算孤儿，
所以用 ``--min-age-hours`` 保护正在进行的上传（默认 6 小时）。

用法（在 buddy-api 容器里跑）：

    python scripts/purge_orphan_import_objects.py
    python scripts/purge_orphan_import_objects.py --apply
    python scripts/purge_orphan_import_objects.py --line mineru --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.minio_client import delete_objects_by_prefix, get_minio  # noqa: E402
from app.config import settings  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

MINERU_ROOT = "corpus/imports/"
B35_ROOT = "import35b/"


def object_prefix(object_name: str, root_depth: int) -> str:
    """把对象名收敛到「单次导入自己的目录」这一层。

    ``root_depth`` 是线的根前缀有几段（``corpus/imports/`` 是 2，``import35b/`` 是 1）；
    根下面一段就是单次导入的目录，再往上是共享层级，绝不能删到那一层。上传暂存区
    多一层（``.../staging/<hex>/``），所以要多算一段。
    """
    parts = [p for p in object_name.split("/") if p]
    if len(parts) <= root_depth + 1:
        return ""
    depth = root_depth + 1
    if parts[root_depth] == "staging":
        depth += 1
    if len(parts) <= depth:
        return ""
    return "/".join(parts[:depth]) + "/"


def _mineru_live_prefixes(session) -> set[str]:
    from app.db.models import CorpusImportJob
    from app.services.corpus_finalize import owned_minio_prefix

    live: set[str] = set()
    for job in session.query(CorpusImportJob).all():
        prefix = owned_minio_prefix(job)
        if prefix:
            live.add(prefix)
    return live


def _b35_live_prefixes(session) -> set[str]:
    from app.db.models import Import35bJob
    from app.import_35b.assets import job_minio_prefixes

    live: set[str] = set()
    for job in session.query(Import35bJob).all():
        live.update(job_minio_prefixes(job.minio_key or "", job.id))
    return live


def _scan(root: str, live: set[str], cutoff: datetime) -> tuple[dict, dict, int]:
    """按前缀汇总孤儿对象；返回 (孤儿, 太新跳过的, 在用对象数)。"""
    client = get_minio()
    root_depth = len([p for p in root.split("/") if p])
    orphans: dict[str, dict] = defaultdict(lambda: {"objects": 0, "bytes": 0})
    too_new: dict[str, dict] = defaultdict(lambda: {"objects": 0, "bytes": 0})
    in_use = 0
    for obj in client.list_objects(settings.minio_bucket, prefix=root, recursive=True):
        prefix = object_prefix(obj.object_name, root_depth)
        if not prefix or prefix in live or any(p.startswith(prefix) for p in live):
            in_use += 1
            continue
        modified = obj.last_modified
        bucket = too_new if modified and modified > cutoff else orphans
        bucket[prefix]["objects"] += 1
        bucket[prefix]["bytes"] += int(obj.size or 0)
    return dict(orphans), dict(too_new), in_use


def _report(line: str, orphans: dict, too_new: dict, in_use: int) -> float:
    total_mb = sum(v["bytes"] for v in orphans.values()) / 1e6
    print(f"\n[{line}] 在用对象 {in_use} 个；孤儿前缀 {len(orphans)} 个，共 {total_mb:.1f} MB")
    for prefix in sorted(orphans, key=lambda p: -orphans[p]["bytes"]):
        row = orphans[prefix]
        print(f"  {prefix:60} {row['objects']:>6} 个  {row['bytes'] / 1e6:>8.1f} MB")
    if too_new:
        skipped_mb = sum(v["bytes"] for v in too_new.values()) / 1e6
        print(f"  （跳过 {len(too_new)} 个太新的前缀，共 {skipped_mb:.1f} MB，可能是正在进行的上传）")
    return total_mb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--line", choices=["mineru", "b35", "all"], default="all")
    parser.add_argument("--apply", action="store_true", help="真删；不加只报告")
    parser.add_argument("--min-age-hours", type=float, default=6.0)
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.min_age_hours)
    lines: list[tuple[str, str, object]] = []
    if args.line in ("mineru", "all"):
        lines.append(("mineru", MINERU_ROOT, _mineru_live_prefixes))
    if args.line in ("b35", "all"):
        lines.append(("b35", B35_ROOT, _b35_live_prefixes))

    removed_total = 0
    freed_total = 0.0
    with get_session_factory()() as session:
        for name, root, live_fn in lines:
            live = live_fn(session)
            orphans, too_new, in_use = _scan(root, live, cutoff)
            freed_total += _report(name, orphans, too_new, in_use)
            if not args.apply:
                continue
            for prefix in sorted(orphans):
                removed = delete_objects_by_prefix(prefix)
                removed_total += removed
                print(f"  删除 {prefix} → {removed} 个对象")

    if args.apply:
        print(f"\n已删除 {removed_total} 个对象，释放约 {freed_total:.1f} MB")
    else:
        print(f"\n仅报告：可释放约 {freed_total:.1f} MB。确认后加 --apply 真删。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
