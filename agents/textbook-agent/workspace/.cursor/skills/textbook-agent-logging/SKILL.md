---
name: textbook-agent-logging
description: 教材 Agent 日志落盘位置、事件 kind、新工具该记什么。
---

# 教材 Agent 日志

全文见仓库 `doc/textbook-agent-logging.md`。容器只写 `/data/agent`，日志在 `logs/`。

## 人读 / 机读

- `logs/agent.log`：20MB × 10，行首带 `[trace=... book=... origin=...]`
- `logs/events.jsonl`：一行一个 JSON，字段 `kind`
- 出错才把全文放到 `logs/payloads/<日期>/<trace_id>/`
- 审计整份：`logs/audit/<book_id>-<时间>.json`

## 串一条导入

同一 `trace_id`：`task.gate` → `tool.start/end` → `toc.compute` → `toc.write` → `sync.decide` → `watch.fire`。

## 新工具该记什么

- 自包含工具：把 `levels` / `toc_diff` / `skipped` / `ok` 放进返回值，`_wrap` 会写成 `tool.end` 和 `toc.compute`。
- 写盘工具：在 Agent 进程里调 `event("toc.write", ...)`，失败时 `dump_payload`。
- 不要 `print`，不要另起日志文件。
