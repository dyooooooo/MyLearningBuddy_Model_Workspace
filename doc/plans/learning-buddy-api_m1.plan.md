# learning-buddy-api M1 — OpenAPI contract (GX10)

**Service**: `http://192.168.3.200:8100` · default student `dev-student-1`

## Layers

| Layer | Storage | Notes |
|-------|---------|-------|
| A | SQLite `homework_entries`, `question_entities` | Ingest from c2-gateway finalize |
| B | SQLite `memory_events` | HANDWRITING_GROWTH, PAGE_FINALIZED, QUESTION_TAGGED, MARKED_WRONG |
| A⁺ | SQLite `mastery_views` | kp_id primary key; `demo=false` |
| RAG | Chroma + bge-small-zh | `data/syllabus/math_hs.json`, `data/question-bank/math_hs.jsonl` |

## Key endpoints

- `GET /api/v1/system/health`
- `POST /api/v1/students/{id}/ingest/homework`
- `POST /api/v1/students/{id}/ingest/events`
- `GET /api/v1/students/{id}/homework`
- `PATCH /api/v1/students/{id}/questions/{qid}` — `{ "is_wrong": true }`
- `GET /api/v1/students/{id}/mastery`
- `GET /api/v1/rag/search?q=...`

## Gateway proxy (for /test/)

- `GET /api/v1/companion/homework` → Buddy list
- `GET /api/v1/companion/buddy/mastery` → full kp mastery JSON
- `PATCH /api/v1/companion/questions/{qid}` → mark wrong

## Android alignment (later)

Room tables should mirror `question_entities`, `memory_events`, `mastery_views` field names.
