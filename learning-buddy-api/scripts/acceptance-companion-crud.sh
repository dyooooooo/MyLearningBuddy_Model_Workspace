#!/usr/bin/env bash
# M1 companion CRUD: homework delete/patch, wrong-book dual list, mastery POST/DELETE, locale.
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8000}"
API_TOKEN="${API_TOKEN:-change-me-api}"
STUDENT="${STUDENT:-student-01}"

auth=(-H "Authorization: Bearer ${API_TOKEN}")
student_hdr=(-H "X-Student-Id: ${STUDENT}")
companion="${GATEWAY_URL}/api/v1/companion"

echo "== M1 companion CRUD acceptance @ ${BUDDY_URL} =="

HW_ID="crud-hw-$(date +%s)"
Q1="${HW_ID}__q__1"
Q2="${HW_ID}__q__2"

curl -sf -X POST "${BUDDY_URL}/api/v1/students/${STUDENT}/ingest/homework" \
  "${auth[@]}" "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d "{
    \"id\": \"${HW_ID}\",
    \"session_id\": \"crud-session\",
    \"page_id\": \"p1\",
    \"finalized_at\": \"2026-06-20T14:00:00+00:00\",
    \"source_frame_ids\": [\"f1\"],
    \"parts\": [
      {\"kind\": \"question\", \"id\": \"1\", \"print_text\": \"解 x+1=2\", \"handwriting\": \"x=1\"},
      {\"kind\": \"question\", \"id\": \"2\", \"print_text\": \"解 x-1=0\", \"handwriting\": \"x=2\"}
    ],
    \"meta\": {\"subject\": \"math\", \"grade_band\": \"hs\", \"title\": \"CRUD test\"}
  }" >/dev/null

echo "Ingested ${HW_ID}"

echo "== QuestionOut eval fields =="
curl -sf "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}" \
  "${auth[@]}" "${student_hdr[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
q = (d.get('questions') or [])[0]
for key in ('eval_json', 'agent_is_wrong', 'wrong_source', 'eval_status'):
    assert key in q, f'missing {key}'
print('OK: QuestionOut eval fields present')
"

echo "== PATCH homework meta =="
curl -sf -X PATCH "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}" \
  "${auth[@]}" "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d '{"meta": {"title": "CRUD patched", "note": "acceptance"}}' | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('meta', {}).get('title') == 'CRUD patched', d
print('OK: homework PATCH meta')
"

echo "== wrong-book dual list (manual add) =="
curl -sf -X PATCH "${BUDDY_URL}/api/v1/students/${STUDENT}/questions/${Q2}" \
  "${auth[@]}" "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d '{"is_wrong": true}' >/dev/null

curl -sf "${BUDDY_URL}/api/v1/students/${STUDENT}/wrong-book" \
  "${auth[@]}" "${student_hdr[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for key in ('items', 'candidates', 'count', 'candidate_count'):
    assert key in d, f'missing {key}'
items = d['items']
assert any(i.get('question_id') == '${Q2}' for i in items), items
assert d['count'] == len(items)
print('OK: wrong-book items=', d['count'], 'candidates=', d['candidate_count'])
"

echo "== restore_agent =="
curl -sf -X PATCH "${BUDDY_URL}/api/v1/students/${STUDENT}/questions/${Q2}" \
  "${auth[@]}" "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d '{"restore_agent": true}' >/dev/null

echo "== mastery POST/DELETE =="
KP="math.hs.sets.complement"
curl -sf -X POST "${BUDDY_URL}/api/v1/students/${STUDENT}/mastery" \
  "${auth[@]}" "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d "{\"kp_id\": \"${KP}\", \"mastery\": \"learning\"}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('kp_id') == '${KP}', d
print('OK: mastery POST', d.get('kp_id'))
"

curl -sf -X DELETE "${BUDDY_URL}/api/v1/students/${STUDENT}/mastery/${KP}" \
  "${auth[@]}" "${student_hdr[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('deleted') == '${KP}', d
print('OK: mastery DELETE')
"

echo "== syllabus-kps locale=en =="
curl -sf "${BUDDY_URL}/api/v1/rag/syllabus-kps?subject=math&grade_band=hs" \
  "${auth[@]}" -H "X-Locale: en" | python3 -c "
import sys, json
rows = json.load(sys.stdin)
assert rows, 'empty syllabus-kps'
r = rows[0]
assert r.get('label'), r
li = r.get('label_i18n') or {}
assert li.get('en') or r.get('label'), li
print('OK: syllabus-kps en label', r.get('label')[:40])
"

echo "== gateway wrong-book proxy =="
curl -sf "${companion}/wrong-book" "${student_hdr[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'items' in d and 'candidates' in d, d
print('OK: gateway wrong-book proxy')
"

echo "== DELETE homework =="
curl -sf -X DELETE "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}" \
  "${auth[@]}" "${student_hdr[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('deleted') == '${HW_ID}', d
print('OK: homework DELETE')
"

code=$(curl -s -o /dev/null -w "%{http_code}" \
  "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}" \
  "${auth[@]}" "${student_hdr[@]}")
if [ "$code" != "404" ]; then
  echo "FAIL: homework still exists after delete (HTTP ${code})"
  exit 1
fi
echo "OK: homework gone (404)"

echo "PASS: acceptance-companion-crud"
