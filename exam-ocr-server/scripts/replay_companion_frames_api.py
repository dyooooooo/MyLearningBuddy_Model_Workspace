#!/usr/bin/env python3
"""Replay companion frames through live gateway API (production path)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _post_multipart(
    url: str,
    *,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    headers: dict[str, str],
) -> dict:
    boundary = f"----replay{int(time.time() * 1000)}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    data = file_path.read_bytes()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode()
    )
    body.extend(b"Content-Type: image/jpeg\r\n\r\n")
    body.extend(data)
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            **headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode())


def _post_multipart_batch(
    url: str,
    *,
    file_paths: list[Path],
    headers: dict[str, str],
) -> dict:
    boundary = f"----replay{int(time.time() * 1000)}"
    body = bytearray()
    for path in file_paths:
        data = path.read_bytes()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode()
        )
        body.extend(b"Content-Type: image/jpeg\r\n\r\n")
        body.extend(data)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            **headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode())


def _post_json(url: str, headers: dict[str, str], body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _summarize_timings(frames: list[dict], *, batch_total_ms: int | None = None) -> None:
    seq_total = 0
    for i, fr in enumerate(frames, start=1):
        ocr = fr.get("ocr_ms") or fr.get("duration_ms") or 0
        refine = fr.get("refine_ms") or 0
        total = fr.get("total_ms") or (ocr + refine)
        seq_total += total
        print(
            f"  frame {i}: ocr={ocr/1000:.1f}s refine={refine/1000:.1f}s "
            f"total={total/1000:.1f}s action={fr.get('scheduler_action')}"
        )
    if batch_total_ms is not None:
        print(
            f"\nBatch wall: {batch_total_ms/1000:.1f}s "
            f"(sequential sum {seq_total/1000:.1f}s)"
        )


def _summarize_session(session: dict) -> None:
    merged = session.get("merged_parts") or []
    print("\n=== merged_parts handwriting ===")
    for p in merged:
        if p.get("kind") != "question":
            continue
        hw = (p.get("handwriting") or "").strip()
        bb = p.get("bbox_handwriting") or []
        flag = "ok" if hw and bb else ("empty" if not hw else "⚠ no bbox")
        print(f"  Q{p.get('id')}: {hw[:70]!r} [{flag}]")
    qs = [p for p in merged if p.get("kind") == "question" and (p.get("handwriting") or "").strip()]
    print(f"\nTotal questions with handwriting: {len(qs)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--student-id", default="student-01")
    parser.add_argument(
        "--frames-dir",
        type=Path,
        required=True,
        help="Directory with frame1.jpg, frame2.jpg, ...",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="POST all frames in one batch (staggered OCR/refine pipeline)",
    )
    args = parser.parse_args()

    headers = {"X-Student-Id": args.student_id}
    base = args.base_url.rstrip("/")

    try:
        created = _post_json(f"{base}/api/v1/companion/sessions", headers, {})
    except urllib.error.HTTPError as e:
        print(e.read().decode(), file=sys.stderr)
        return 1

    session = created["session"]
    sid = session["id"]
    print(f"Session {sid} created (REFINE via gateway env)")

    frames = sorted(args.frames_dir.glob("frame*.jpg"))
    if not frames:
        print("No frame*.jpg found", file=sys.stderr)
        return 1

    last = session
    if args.batch:
        print(f"\nPosting batch ({len(frames)} frames) …")
        t0 = time.perf_counter()
        try:
            resp = _post_multipart_batch(
                f"{base}/api/v1/companion/sessions/{sid}/frames/batch",
                file_paths=frames,
                headers={**headers, "X-Already-Normalized": "1"},
            )
        except urllib.error.HTTPError as e:
            print(e.read().decode(), file=sys.stderr)
            return 1
        elapsed = time.perf_counter() - t0
        last = resp.get("session") or last
        print(f"  batch_total_ms={resp.get('batch_total_ms')} client_elapsed={elapsed:.1f}s")
        _summarize_timings(resp.get("frames") or [], batch_total_ms=resp.get("batch_total_ms"))
    else:
        for i, path in enumerate(frames, start=1):
            print(f"\nPosting frame {i}: {path.name} …")
            t0 = time.perf_counter()
            try:
                resp = _post_multipart(
                    f"{base}/api/v1/companion/sessions/{sid}/frames",
                    fields={},
                    file_field="file",
                    file_path=path,
                    headers={**headers, "X-Already-Normalized": "1"},
                )
            except urllib.error.HTTPError as e:
                print(e.read().decode(), file=sys.stderr)
                return 1
            elapsed = time.perf_counter() - t0
            last = resp.get("session") or last
            print(
                f"  frame_id={resp.get('frame_id')} action={resp.get('scheduler_action')} "
                f"ocr_ms={resp.get('ocr_ms')} refine_ms={resp.get('refine_ms')} "
                f"total_ms={resp.get('total_ms')} elapsed={elapsed:.1f}s"
            )

    _summarize_session(last)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
