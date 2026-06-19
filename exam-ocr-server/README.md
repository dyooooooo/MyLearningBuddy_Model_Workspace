# exam-ocr-server (Spark C2 Gateway)

FastAPI C2 gateway for Spark dual-Docker deployment.

- Calls vLLM OpenAI-compatible vision API (`VLLM_BASE_URL`)
- Single endpoint: per-question print + handwriting

## Test UI

```text
http://192.168.3.200:8000/test/
```

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/system/health` | GET | Gateway + vLLM health |
| `/api/v1/ocr/page` | POST multipart | Page analyze (print + handwriting per question) |

### Response

```json
{
  "task": "page_analyze",
  "parts": [
    { "kind": "header", "print": "试卷标题、注意事项…", "bbox_print": [...] },
    { "kind": "section", "print": "一、填空题", "bbox_print": [...] },
    {
      "kind": "question",
      "id": "1",
      "print": "印刷题干",
      "handwriting": "手写答案",
      "bbox_print": [...],
      "bbox_handwriting": [...]
    }
  ]
}
```

## Deploy

```bash
cd ~/workspace/learning-buddy-c2
```

```bash
docker compose build c2-gateway
```

```bash
docker compose up -d
```
