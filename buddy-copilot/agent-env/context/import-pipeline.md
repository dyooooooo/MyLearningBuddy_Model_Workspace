# 导入链路地图（MinerU 线）

```text
Studio 上传 PDF → c2-gateway /api/v1/studio/import/pdf → MinIO(corpus/imports/)
  → buddy-api POST /api/v1/admin/corpus/import/pdf {minio_key, edition_id, ...}
  → Redis 导入队列 → buddy-worker：
       MinerU Cloud 解析 → mineru_adapter → IR
       → 数学富化 → 几何特征（features.py）
       → 声明式规则引擎（base_rules.yaml < <edition>/layout_rules.yaml < overrides/pages/）
       → decide.py 判 zone；低置信页写 job.meta.layout_escalate_pages
       → document_ir 渲染 HTML → corpus_nodes 落库
```

主链**永不**调用 35B refine：高置信页由几何 + 规则直接过，低置信页升级给 Cursor SDK。

## 关键文件/函数

| 职责 | 路径 |
|---|---|
| 导入路由（start/poll/blocks/html/reprocess） | `learning-buddy-api/app/routes/corpus_admin.py` |
| 全书主链编排 | `learning-buddy-api/app/services/import_ir_pipeline.py` |
| Cloud JSON → IR | `learning-buddy-api/app/services/ir/mineru_adapter.py` |
| 几何特征计算 | `learning-buddy-api/app/services/ir/layout/features.py` |
| 规则加载与层叠 | `learning-buddy-api/app/services/ir/layout/rules.py` |
| zone 判定 + escalate | `learning-buddy-api/app/services/ir/layout/decide.py` |
| 共享硬不变量规则 | `learning-buddy-api/configs/layout/base_rules.yaml` |
| 分栏（遗留路径） | `learning-buddy-api/app/services/ir/column_detect.py` · `layout_enrich.py` |
| HTML 骨架 / TOC / 页眉 | `learning-buddy-api/app/services/document_ir.py` |
| 页重处理（35B 系列 mode 已封禁，见 `rules/40`） | `learning-buddy-api/app/services/corpus_reprocess_service.py` |
| worker 队列消费 | `learning-buddy-api/app/jobs/worker.py` |

## 导入 API 要点

- `POST /admin/corpus/import/pdf` 必填 `minio_key` + `edition_id`（方案在任务创建时绑定，
  存 `corpus_import_jobs.edition_id` 与 meta）。可选 `page_limit`（前 N 页试跑）。
- 轮询 `GET /admin/corpus/import/jobs/{job_id}`：status
  `uploaded→converting→parsed→chunking→chunked→indexed`，失败 `failed`；
  meta 带 `pipeline_phase` / `pages_ready` / `blocks_key` / `layout_escalate_pages`。
- 整书 IR 存 MinIO `blocks.json`（meta.blocks_key）；页 HTML 存 corpus_nodes 草稿 chunk。

## 资源访问纪律

- **方案（Edition）**：草稿在 Postgres（走 `editions.*` API），生效文件在
  `learning-buddy-api/configs/editions/<id>/`——含 `config.json`、`layout_rules.yaml`、
  `overrides/pages/`（bind-mount，便宜通道）。
- **中间结果**：页 IR / 页 HTML / 任务状态一律走 buddy-api 管理接口（只读工具），
  不直连 Postgres / MinIO / Redis。
- **完整资源地图**以 `resources.yaml` 为准（what / where / how / mutability 四字段）。

## 双线隔离

35B 线（`import35b` 队列 / `import35b/` 前缀 / `/proxy35b`）与 MinerU 线完全隔离，勿混。
`policy.entries` 只对 35B 线的 `b35.vision_layout` 槽位有效，对 MinerU 线无效。

## 部署形态

buddy-api / buddy-worker 代码打进镜像；bind-mount 只有
`configs/editions/`（含 `layout_rules.yaml` 与 `overrides/`）、
`configs/textbook_profiles/`（只读）、logs、`app/static`（api，只读）。
改 `app/**` 或 `data/prompts/**` → 必须 rebuild api+worker 两个服务。
改 `configs/editions/**` 的规则 YAML → 不用 rebuild，`corpus.rebuild_ir` 即可。
