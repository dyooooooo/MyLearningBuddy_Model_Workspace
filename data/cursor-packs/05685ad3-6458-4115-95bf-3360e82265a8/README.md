# 35B Repair Pack · 05685ad3-6458-4115-95bf-3360e82265a8

## 现象（用户）
- 任务: 高一数学上_35b · job `b9cf8767-5a90-4d7e-8b56-37275252a345` · 第 7 页
- 描述: 将内容识别成了表格
- 截图: [images/user_screenshot.jpg](images/user_screenshot.jpg)

## 系统体检（T1）
- 共 0 条: 无
- 详情: [evidence/t1_findings.json](evidence/t1_findings.json)

## 初步归因（执行前请确认）
- 层级: **harness**（置信度 low）
- 依据: 用户有描述但 T1 未检出，可能需补规则或 prompt；建议 Plan 模式先细化
- 优先文件:
- `learning-buddy-api/app/import_35b/validation/rules.py`
- `learning-buddy-api/data/eval/`

## 证据链
1. [images/user_screenshot.jpg](images/user_screenshot.jpg)
2. [evidence/raw_model.json](evidence/raw_model.json)
3. [evidence/blueprint.json](evidence/blueprint.json)
4. [evidence/page.html](evidence/page.html)
5. [analysis/diff_hints.md](analysis/diff_hints.md)

## 入口
- 计划: [PLAN.prompt.md](PLAN.prompt.md)
- 验收: [commands.sh](commands.sh)
