from __future__ import annotations


def cancel_task(task_id: str) -> dict:
    """Disable a task and stop its running jobs."""
    from app import tasks

    return tasks.cancel_task(task_id)
