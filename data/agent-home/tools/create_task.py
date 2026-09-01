from __future__ import annotations


def create_task(
    title: str,
    mode: str = "trigger",
    trigger: str = "",
    cron: str = "",
    message: str = "",
    condition: str = "",
) -> dict:
    """Create a workspace task. mode is schedule or trigger; message is sent to the Agent.
    Trigger tasks may set condition: a sentence the Agent judges before running."""
    from app import tasks

    return tasks.create_task(
        title=title,
        mode=mode,
        trigger=trigger,
        cron=cron,
        message=message,
        condition=condition,
    )
