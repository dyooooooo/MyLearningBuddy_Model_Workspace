# Golden eval cases — see ai-four-pillars skill (Harness)

Add JSONL cases here; run via scripts/acceptance-buddy-api.sh

## Math segments

- `math_segments_golden.jsonl` — Unicode/plain → Segment[] + LaTeX validation
- Run: `scripts/acceptance-math-segments.sh`
- Backfill existing DB: `PYTHONPATH=. python3 scripts/backfill_math_segments.py --student bowen`

## 35B import (layout_blueprint_v5)

- `35b_schema_v5_golden.json` — v5 acceptance thresholds + root causes + problem pages
- `35b_inline_math_v5_golden.jsonl` — v5 fixtures: `$...$` inline LaTeX, AMS symbols
  (`\leqslant`/`\varnothing`), equation server-validation, MFR degeneration, and
  figure-caption-body-text reclassification cases
- Harness: `app/import_35b/harness_golden.py`
- Run: `scripts/acceptance-import35b-v5.sh` (includes v3 structural tests)
- Legacy: `35b_schema_v4_golden.json`, `35b_inline_math_v4_golden.jsonl`, `35b_schema_v3_golden.json`
