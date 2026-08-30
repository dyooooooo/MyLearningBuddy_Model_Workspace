你可以用工作区任务工具给人（或给自己）建任务。任务写在 tasks/tasks.json，和侧栏「任务」是同一份。

两种 mode（没有手动）：
- schedule：到点把「对话」发给 Agent（本机时区；每天 / 每周 / 每月 / 指定日期）
- trigger：事件发生时发给 Agent。事件以任务页目录为准（系统 / 任务完成 / 本机工具），例如 `kb.import.completed`（导入教材完成后）、`task.<任务id>.completed`、`tool.<工具id>`。payload 常带 book_id
- 拖入 PDF：MinerU 先认前三页，identify_message 带上这段文本让 Agent 抽科目学段；点「开始识别」后才走清洗（message）。payload 可带 conversation_id 续上原对话。

对话是一段普通文字，丢给 AI Agent 去执行。触发里可以用 `{{book_id}}` 表示刚导入的书号。

工具：create_task、list_tasks、update_task、cancel_task。

任务对话里没明文要求改代码、添加工具或加钩子时，不要改 tools/、surfaces/、钩子、技能。新功能不要改已有工具。要改代码时先 request_code_edit 交四段说明，等用户在授权卡上点「授权」后再 write_tool 新建工具，并用 create_task(mode=trigger) 挂到 tool.<id> 或 kb.import.completed 等已有事件。一书数据只改该书 pages。见 .cursor/rules/open-closed.mdc。
