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
