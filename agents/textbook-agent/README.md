# Textbook File Agent

独立 HTTP 服务：用 Cursor SDK 读 `textbooks/` 下的 PDF 与 Markdown，不建向量库。

```bash
export AGENT_HOME=/tmp/agent-home
export TEXTBOOK_AGENT_TOKEN=dev-token
export CURSOR_API_KEY=
python -m app.main
```

```bash
curl -s http://127.0.0.1:8200/health
```

```bash
curl -s -H "Authorization: Bearer dev-token" -X POST http://127.0.0.1:8200/v1/grade \
  -H "Content-Type: application/json" \
  -d '{"request_id":"t1","problem_text":"正整数","student_answer":"{0}"}'
```

鉴权后轮询 `GET /v1/jobs/{job_id}`。配置：`GET /v1/config`。

## 故障归类的硬规矩（别再退回去）

Cursor 的错误文案不可信，官方已确认两处误标：一个从有效 API key 换来的短时令牌
过期，会报成 `Authentication error … try logging out and back in`；容器里被 NAT
掐断的 HTTP/2 长连接，会报成 `Connection stalled repeatedly`。历史上反复出现的
「登录失效」误报，全部来自拿这些文案当结论。

因此：

- **`AUTH_ZH`（登录失效）只有一个生产者** —— `cursor_auth` 里一次真实的
  `Cursor.me()` 拒绝。任何运行流里的文案都不得产出它。这条由
  `tests/test_sdk_errors.py::test_no_vendor_text_can_reach_auth_zh` 把住。
- **未识别的错误落到中性文案 + 原文**（`CLOUD_ZH`），不要猜。新增分类请配对新增
  测试，而不是往 `humanize_sdk_error` 里再堆一个字符串分支。
- **令牌过期的唯一有效恢复是重开本地桥进程**（`recover_bridge`）后
  `Agent.resume` 同一个 agent。重写 `auth.json` 对 API-key 宿主无效，官方明确说过。
- **`cursor-sdk` 必须钉死版本**：wheel 里带着内嵌的 node 桥，浮动版本等于让
  Docker 层缓存替你选运行时。
- **agent 流走 HTTP/1.1**（`CURSOR_USE_HTTP1`，经 `local.useHttp1ForAgent` 下传）。
  实测同一对话两轮从 143s / 超时降到 15s / 9s。
