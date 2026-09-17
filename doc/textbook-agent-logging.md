# textbook-agent 日志

教材 Agent 容器 `read_only: true`，只能写 `/data/agent`（宿主机 `data/agent-home/`）。日志都在这里，不进 tmpfs。

## 文件

| 路径 | 用途 | 轮转 |
|---|---|---|
| `logs/agent.log` | 人读文本，行首带 `[trace=... book=... origin=...]` | 20MB × 10 |
| `logs/events.jsonl` | 机读事件，一行一个 JSON | 20MB × 10 |
| `logs/payloads/<日期>/<trace_id>/` | 出错或审计不通过时的全文 | 保留 30 天 |
| `logs/audit/<book_id>-<时间>.json` | `audit_toc` 整份报告 | 保留 30 天 |
| `traces/` | job 轨迹 | 保留 30 天 |
| `logs/hooks.log` | 钩子原文 | 超过 20MB 转存 `hooks.log.1` |

清理挂在已有 `task-tick` 线程上每天跑一次，不另起线程。

## 事件 `kind`

| kind | 何时 | 关键字段 |
|---|---|---|
| `tool.start` / `tool.end` | 每个工具进出 `_wrap` | `tool` `status` `ms` `args`/`result` 摘要；失败有 `payload` |
| `toc.compute` | 工具返回里带 `levels` / `entries` | `levels` `toc_diff` `skipped` |
| `toc.write` | `write_toc_pages` 写一页 | 页号、写前后 sha、`unmapped`；拒写时 `warning=shrink_guard`（现有行数 vs 将写行数），payload 落 `logs/payloads/.../toc-shrink-before-*.md` 与 `toc-shrink-after-*.md` |
| `toc.level` | 渲染器逐行（经 node logs） | 标签、印张页、查表/启发式、层级 |
| `toc.audit` | `audit_toc` | 报告路径与计数 |
| `task.gate` | `start_task` 比对 `when` | `when` `actual` `matched` |
| `sync.decide` | `page_sync` 选分支 | 脏标记、围栏去留 |
| `sync.fence_drop` | `_find_block` 找不到围栏 | 页号 |
| `watch.fire` | 目录页被 watchdog 触发 | 距上次写盘毫秒、上次 `trace_id` |
| `page.read` / `surface.prepare` | 读页与渲染 | entries 条数、缓存、耗时 |
| `log.cleanup` | 日清理 | `removed` `hooks_rotated` |

`status`：`ok` / `skipped` / `error`。下游任务可用 `when.status` 过滤。

## 一次导入怎么串

buddy-api 导入完成时若带 `X-Trace-Id` 会继承，否则 Agent 自造。同一 `trace_id` 贯穿：

```text
task.gate → job → tool.start/end → toc.compute → toc.write → sync.decide → watch.fire
```

`page_watch` 自己开新 `trace_id`，但 `origin=watch`，并带上这一页上次写盘的 `trace_id`。

## 排障

目录层级不对：

```bash
grep '"kind": "toc.level"' data/agent-home/logs/events.jsonl | jq 'select(.from=="miss" or .from=="guess")'
```

目录页被冲掉：

```bash
grep '"kind": "watch.fire"' data/agent-home/logs/events.jsonl | jq .
```

```bash
grep '"kind": "sync.decide"' data/agent-home/logs/events.jsonl | jq .
```

任务没触发：

```bash
grep '"kind": "task.gate"' data/agent-home/logs/events.jsonl | jq 'select(.matched==false)'
```

按一次导入过滤：

```bash
jq 'select(.trace_id=="TRACE")' data/agent-home/logs/events.jsonl
```
