#!/usr/bin/env python3
"""Thin CLI for the textbook Agent HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Textbook Agent client")
    parser.add_argument("--url", default=os.environ.get("TEXTBOOK_AGENT_URL", "http://127.0.0.1:8210"))
    parser.add_argument("--token", default=os.environ.get("TEXTBOOK_AGENT_TOKEN", ""))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    g = sub.add_parser("grade")
    g.add_argument("--problem", required=True)
    g.add_argument("--answer", default="")
    g.add_argument("--hint", default="")
    g.add_argument("--request-id", default="cli-1")
    c = sub.add_parser("chat")
    c.add_argument("message")
    args = parser.parse_args()
    headers = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    with httpx.Client(timeout=30.0) as client:
        if args.cmd == "health":
            r = client.get(f"{args.url}/health")
            r.raise_for_status()
            print(r.text)
            return 0
        if args.cmd == "grade":
            r = client.post(
                f"{args.url}/v1/grade",
                headers=headers,
                json={
                    "request_id": args.request_id,
                    "problem_text": args.problem,
                    "student_answer": args.answer,
                    "textbook_hint": args.hint,
                },
            )
        else:
            r = client.post(
                f"{args.url}/v1/chat",
                headers=headers,
                json={"message": args.message},
            )
        r.raise_for_status()
        job_id = r.json()["job_id"]
        for _ in range(120):
            j = client.get(f"{args.url}/v1/jobs/{job_id}", headers=headers)
            j.raise_for_status()
            body = j.json()
            if body["status"] in {"completed", "failed"}:
                print(json.dumps(body, ensure_ascii=False, indent=2))
                return 0 if body["status"] == "completed" else 2
            time.sleep(2)
    print("timeout", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
