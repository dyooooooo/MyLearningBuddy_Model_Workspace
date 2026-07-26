# 35B Repair Plan · 05685ad3-6458-4115-95bf-3360e82265a8

> 来源: 高一数学上_35b p7 · prompt `layout_blueprint_v5`

## 目标
修复第 7 页：将内容识别成了表格

## 约束
- 只改 35B 线：`learning-buddy-api/app/import_35b/**`（及 Studio 渲染如需）
- 禁止动 exam-ocr ③ / 旧 HITL 裁决路径
- 改 pipeline 后：`docker compose build buddy-api buddy-worker` 并 `up -d`
- 必须补 golden + 跑 acceptance

## 归因
- 层级: **harness**（low）
- 用户有描述但 T1 未检出，可能需补规则或 prompt；建议 Plan 模式先细化

## 执行步骤

### Step 1 — 复现（只读）
- [ ] 读 `@images/user_screenshot.jpg`
- [ ] 读 `@evidence/raw_model.json` `@evidence/blueprint.json` `@evidence/page.html`
- [ ] `bash commands.sh reproduce`

### Step 2 — 修改
- `learning-buddy-api/app/import_35b/validation/rules.py`
- `learning-buddy-api/data/eval/`

### Step 3 — Harness
- [ ] 将 `harness/golden_stub.jsonl` 合并入 `learning-buddy-api/data/eval/`
- [ ] `bash commands.sh acceptance`

### Step 4 — 部署验证
- [ ] 重建 buddy-api / buddy-worker
- [ ] 重导该教材或 `--page_limit` 单页；确认 p7

## 完成标准
- [ ] golden / replay 通过
- [ ] 本页 T1 硬错误消除（或 golden 显式 expect）
- [ ] 用户描述的问题在预览中消失
