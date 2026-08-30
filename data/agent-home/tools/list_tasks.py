from __future__ import annotations


def list_tasks() -> dict:
    """List workspace tasks (builtin + user)."""
    from app import tasks

    return {"tasks": tasks.list_tasks()}
