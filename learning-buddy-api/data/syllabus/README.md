# Syllabus corpus layout

Each working corpus pair uses `{subject}_{grade_band}.json` in this directory and a matching
`../question-bank/{subject}_{grade_band}.jsonl`.

| File | Status |
|------|--------|
| `math_hs.json` | **Working** — pilot RAG + mastery |
| `math_ms.json` | Placeholder schema only (middle school) |

Subjects other than `math_hs` are reserved for future milestones; ingest rejects missing pairs.
