from __future__ import annotations


def update_task(
    task_id: str,
    title: str = "",
    mode: str = "",
    trigger: str = "",
    cron: str = "",
    message: str = "",
    condition: str = "",
    when: str = "",
    enabled: str = "",
) -> dict:
    """Update a workspace task. Empty string keeps the old value.
    condition is Agent-judged; when is meta.json machine match (JSON or k:v)."""
    from app import tasks

    fields: dict = {}
    if title:
        fields["title"] = title
    if mode:
        fields["mode"] = mode
    if trigger:
        fields["trigger"] = trigger
    if cron:
        fields["cron"] = cron
    if message:
        fields["message"] = message
    if condition:
        fields["condition"] = condition
    if when:
        fields["when"] = when
    if enabled != "":
        fields["enabled"] = str(enabled).strip().lower() in ("1", "true", "yes", "on")
    row = tasks.update_task(task_id, **fields)
    if row is None:
        raise ValueError("task not found")
    return row
