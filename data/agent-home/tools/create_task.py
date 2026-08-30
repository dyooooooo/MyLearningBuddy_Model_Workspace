from __future__ import annotations


def create_task(
    title: str,
    mode: str = "trigger",
    trigger: str = "",
    cron: str = "",
    message: str = "",
) -> dict:
    """Create a workspace task. mode is schedule or trigger; message is sent to the Agent."""
    from app import tasks

    return tasks.create_task(
        title=title,
        mode=mode,
        trigger=trigger,
        cron=cron,
        message=message,
    )
