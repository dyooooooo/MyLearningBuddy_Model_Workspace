---
name: tasks
description: 在工作区建定时或事件触发任务。和侧栏「任务」写同一份 tasks/tasks.json。
---

# 任务

- `create_task(title, mode, trigger?, cron?, message?)`
- `list_tasks()`
- `update_task(task_id, …)` 改名称/方式/cron/事件/对话
- `cancel_task(task_id)` 停用并取消在跑的 job

`mode` 只有 `schedule` 或 `trigger`（没有手动）。

触发事件以任务页目录为准：

- 系统：`kb.import.completed`、`kb.import.dropped`
- 工具：`tool.<工具id>`（工具被调用后自动发出，例如 `tool.build_toc`）
- 任务完成：`task.<任务id>.completed`

对话正文可用 `{{book_id}}`。

## 新功能怎么接

任务对话里没明文要求改代码、添加工具或加钩子时，不要改 `tools/`、`surfaces/`、钩子、技能。不要改已有工具来加功能。要改代码时先 `request_code_edit` 并等用户在授权卡上点授权，再 `write_tool` 新建工具，然后 `create_task(mode=trigger, trigger=tool.<新id>` 或上面的系统事件）。一书数据问题只改该书 pages。见 `.cursor/rules/open-closed.mdc`。
