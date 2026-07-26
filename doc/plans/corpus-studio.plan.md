# Corpus Studio — 知识库运营台

> Phase 1–3 实施规格。选型见执行计划（D1–D8 已确认）。

## 定位

- **用户**：教研/运营
- **对象**：A 通用库（`corpus_nodes` + Milvus）
- **入口**：`http://<spark>:8000/studio/`
- **API**：`learning-buddy-api` `/api/v1/admin/corpus/*`（admin_token）

## 存储分层

| 层 | 技术 | 内容 |
|----|------|------|
| L1 | Milvus `lb_corpus` | published 向量（生产检索） |
| L1s | Milvus `lb_corpus_staging` | draft/review 试搜 |
| L2 | PG `corpus_*` | 全文、图谱、状态 |
| L3 | MinIO `corpus/imports/` | PDF 原文件（c2 上传） |

## CorpusIngestEnvelope

```json
{
  "ingest_id": "uuid",
  "source_channel": "companion|scout|pdf_import|photo|bilibili|class_screenshot",
  "intent": "corpus_ops|student_learning",
  "student_id": null,
  "subject": "math",
  "grade_band": "hs",
  "status": "received|structuring|draft|review|published|rejected"
}
```

## 表结构

### corpus_import_jobs

| 列 | 类型 | 说明 |
|----|------|------|
| id | PK uuid | |
| status | varchar | uploaded→converting→parsed→chunked→failed |
| source_channel | varchar | pdf_import |
| minio_key | varchar | MinIO 对象键 |
| markdown | text | PyMuPDF 抽取（电子版）或 vLLM 页 OCR（扫描版），经 trim + normalize |

### PDF 导入双通路（2026-06）

| 类型 | 检测 | 转换器 | 说明 |
|------|------|--------|------|
| 电子版 | `avg_chars_per_page >= 80` | `pymupdf` | 定起始页裁剪封面 → 章/节/段落分块 |
| 扫描版 | 文本密度低 | `pymupdf_ocr` | buddy-worker 渲染页图 → vLLM vision → Markdown |
| 降级 | PyMuPDF 失败 | `markitdown_fallback` | 保留 MarkItDown 兼容 |

Harness：`learning-buddy-api/scripts/acceptance-corpus-import.sh`
| textbook_node_id | varchar | 关联 tb:... |
| subject, grade_band | varchar | |
| label | varchar | 教材标题 |
| error | text | |
| meta_json | text | chunk_count, warnings |
| created_at, updated_at | varchar | ISO |

### corpus_inbox_items (Phase 2)

| 列 | 说明 |
|----|------|
| id, scout_run_id | |
| source_url, source_platform | |
| resource_kind | video, article, ... |
| proposed_node_ids_json | draft node ids |
| agent_confidence | float |
| status | pending→ready→approved→rejected |

### corpus_scout_runs (Phase 2)

| 列 | 说明 |
|----|------|
| id, query, subject, grade_band | |
| status, steps_json | LangGraph trace |
| inbox_count | |

## Admin API

前缀：`/api/v1/admin/corpus`，Bearer admin_token。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /stats | PG/Milvus 计数 + milvus_schema_ok |
| GET | /tree | 懒加载导航树 root=textbook\|kp\|method\|shortcut\|question\|import\|inbox |
| GET | /nodes/search | label/summary/id ILIKE 搜索 |
| GET | /nodes | 列表 filter: status, node_type, subject |
| GET | /nodes/{id} | 详情 |
| GET | /nodes/{id}/links | 出链/入链；`?hide=example_for,...` 降噪；响应含 structural/kp/tags/other 分组 |
| GET | /workqueue | draft/review/未索引/failed_imports/inbox 运营队列 |
| POST | /nodes | 新建 draft |
| PATCH | /nodes/{id} | 更新 |
| DELETE | /nodes/{id} | 仅 draft/archived |
| POST | /nodes/bulk-publish | 批量发布 |
| POST | /nodes/{id}/publish-subtree | 发布教材/章子树 |
| POST | /nodes/{id}/set-status | 状态流转 |
| GET | /nodes/{id}/backlinks | 反链 |
| POST | /nodes/{id}/edges | 加边 |
| POST | /nodes/{id}/publish | → Milvus lb_corpus |
| POST | /nodes/{id}/embed-staging | → lb_corpus_staging |
| POST | /search-preview | query + collection=staging\|production |
| POST | /reindex | force rebuild |
| POST | /reindex/async | 异步批量 publish→Milvus，返回 job_id |
| GET | /reindex/jobs/{id} | 异步 reindex 进度 |
| POST | /import/pdf | body: minio_key, subject, grade_band, label |
| GET | /import/jobs | |
| GET | /import/jobs/{id} | |
| PATCH | /import/jobs/{id}/markdown | 人工改 MD 后 re-chunk |
| POST | /import/jobs/{id}/chunk | 生成 section/chunk draft |
| POST | /scout | Phase 2 启动 Scout |
| GET | /inbox | Phase 2 |
| POST | /inbox/{id}/approve | |
| POST | /promote/from-question/{id} | Phase 3 升格 |
| GET | /graph | center, hops, include_draft, rel_types, root |

## UI（Obsidian 三栏 · Vite）

- **源码**：`exam-ocr-server/studio-ui/`（TypeScript + Vite，`base: /studio/`）
- **构建**：`cd studio-ui && npm run build` → `studio-ui/dist/` → Docker 复制到 `static/studio/`
- **布局**：Vault 侧栏（待办队列 + 教材/KP/…/索引健康）+ 中央笔记（容器 TOC / 类型模板）+ Inspector（分组出链 / 索引 CTA）
- **P0–P2**：ContainerOutline TOC · revealInTree · links 分组 · WorkQueue · KpPicker · KaTeX · Wikilink AC · 图谱子图+边过滤 · ReindexProgress · CommandPalette Tab + SearchDrawer
- **图谱**：vis-network，`GET /graph?root=textbook:{id}&rel_types=...`
- **快捷键**：Ctrl+K 全局搜索

## c2-gateway 代理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/studio/import/pdf | multipart → MinIO → buddy import |
| GET | /api/v1/studio/content-packs | 转发 admin content-packs |
| GET | /api/v1/studio/proxy/* | 转发 admin corpus（服务端 token） |

## 发布管线（审核治理 · draft → review → published）

```
新建/导入 → status=draft（PG 图，不进 Milvus）
提交审核 → POST /nodes/{id}/submit-review → review
正式发布 → POST /nodes/{id}/publish（仅 review）→ Milvus lb_corpus
降级     → set-status 离开 published 时自动从 Milvus 删除
清空重建 → POST /reset-workspace（清空 Milvus+PG，data/ 重载为 draft）
```

禁止跳过审核：`publish` / `set-status` 不可直接将 draft 设为 published。

## Harness

- `data/eval/rag_retrieval_cases.jsonl`
- `scripts/acceptance-corpus-studio.sh`
- `scripts/acceptance-corpus-tree.sh`
- `scripts/acceptance-corpus-workqueue.sh`
