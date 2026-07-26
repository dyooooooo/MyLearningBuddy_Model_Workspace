# 35B Repair Plan · 40c28f24-28d2-4450-bbaf-b583de9fbbd6

> 来源: 高一数学上_35b p3 · prompt `layout_blueprint_v5`

## 目标
修复第 3 页：整页空白，但是结构化输出将正文标记为caption

## 约束
- 只改 35B 线：`learning-buddy-api/app/import_35b/**`（及 Studio 渲染如需）
- 禁止动 exam-ocr ③ / 旧 HITL 裁决路径
- 改 pipeline 后：`docker compose build buddy-api buddy-worker` 并 `up -d`
- 必须补 golden + 跑 acceptance

## 归因
- 层级: **compiler**（high）
- T1 MAIN_EMPTY：有 main 块但 lb-main 为空，优先查编译/role-zone 路由

## 执行步骤

### Step 1 — 复现（只读）
- [ ] 读 `@images/user_screenshot.jpg`
- [ ] 读 `@evidence/raw_model.json` `@evidence/blueprint.json` `@evidence/page.html`
- [ ] `bash commands.sh reproduce`

### Step 2 — 修改
- `learning-buddy-api/app/import_35b/page_compiler.py`
- `learning-buddy-api/app/import_35b/render_zone.py`

### Step 3 — Harness
- [ ] 将 `harness/golden_stub.jsonl` 合并入 `learning-buddy-api/data/eval/`
- [ ] `bash commands.sh acceptance`

### Step 4 — 部署验证
- [ ] 重建 buddy-api / buddy-worker
- [ ] 重导该教材或 `--page_limit` 单页；确认 p3

## 完成标准
- [ ] golden / replay 通过
- [ ] 本页 T1 硬错误消除（或 golden 显式 expect）
- [ ] 用户描述的问题在预览中消失
