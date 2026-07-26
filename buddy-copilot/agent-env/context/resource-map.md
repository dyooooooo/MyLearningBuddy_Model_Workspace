# 资源地图（人读版）

机器可读权威版：`buddy-copilot/agent-env/resources.yaml`（what/where/how/mutability 四字段）。

## 一句话版

- **方案（Edition）**：草稿在 Postgres（走 API），种子/发布文件在
  `learning-buddy-api/configs/editions/`（bind-mount，便宜通道）
- **Prompt 模板**：MinerU 线 `learning-buddy-api/data/prompts/`，35B 线
  `learning-buddy-api/app/import_35b/prompts/`（都打进镜像，昂贵通道）
- **中间结果**：页 IR / 页 HTML / 任务状态一律走 buddy-api 管理接口（只读工具），
  不直连 Postgres / MinIO / Redis
- **验收**：golden 走 API；单测在 `learning-buddy-api/tests/`；评测数据 `learning-buddy-api/data/eval/`
- **流水线代码**：adapter / column_detect / document_ir / corpus_reprocess_service
  （见 resources.yaml pipeline_code 段；全部昂贵通道）

## 双线隔离

MinerU（默认队列 / `corpus/imports/` / `/proxy`）↔ 35B（`import35b` 队列 / `import35b/` / `/proxy35b`）。
