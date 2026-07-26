# 导入链路地图（MinerU 线）

```text
Studio 上传 PDF → c2-gateway /api/v1/studio/import/pdf → MinIO(corpus/imports/)
  → buddy-api POST /api/v1/admin/corpus/import/pdf {minio_key, edition_id, ...}
  → Redis 导入队列 → buddy-worker：MinerU Cloud 解析 → mineru_adapter → IR
  → layout_enrich/column_detect 分栏 → document_ir 渲染 HTML → corpus_nodes 落库
```

## 关键文件/函数

| 职责 | 路径 |
|---|---|
| 导入路由（start/poll/blocks/html/reprocess） | `learning-buddy-api/app/routes/corpus_admin.py` |
| Cloud JSON → IR | `learning-buddy-api/app/services/ir/mineru_adapter.py` |
| 分栏 | `learning-buddy-api/app/services/ir/column_detect.py` · `layout_enrich.py` |
| HTML 骨架 / TOC / 页眉 | `learning-buddy-api/app/services/document_ir.py` |
| 页重处理 | `learning-buddy-api/app/services/corpus_reprocess_service.py` |
| worker 队列消费 | `learning-buddy-api/app/jobs/worker.py` |

## 导入 API 要点

- `POST /admin/corpus/import/pdf` 必填 `minio_key` + `edition_id`（方案在任务创建时绑定，
  存 `corpus_import_jobs.edition_id` 与 meta）。可选 `page_limit`（前 N 页试跑）。
- 轮询 `GET /admin/corpus/import/jobs/{job_id}`：status
  `uploaded→converting→parsed→chunking→chunked→indexed`，失败 `failed`；
  meta 带 `pipeline_phase` / `pages_ready` / `blocks_key`。
- 整书 IR 存 MinIO `blocks.json`（meta.blocks_key）；页 HTML 存 corpus_nodes 草稿 chunk。

## 双线隔离

35B 线（import35b 队列 / `import35b/` 前缀 / `/proxy35b`）与 MinerU 线完全隔离，勿混。

## 部署形态

buddy-api / buddy-worker 代码打进镜像；bind-mount 只有
`configs/editions/`、`configs/textbook_profiles/`（只读）、logs、`app/static`（api，只读）。
改 `app/**` 或 `data/prompts/**` → 必须 rebuild api+worker 两个服务。
