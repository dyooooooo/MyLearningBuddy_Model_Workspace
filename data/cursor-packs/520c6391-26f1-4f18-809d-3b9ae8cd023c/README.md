# 35B Repair Pack · 520c6391-26f1-4f18-809d-3b9ae8cd023c

## 现象（用户）
- 任务: 高一数学上_35b · job `b9cf8767-5a90-4d7e-8b56-37275252a345` · 第 4 页
- 描述: 将正文识别为"figure": {
          "caption": "副主编 应坚刚 鲍建生"
        }
- 截图: [images/user_screenshot.jpg](images/user_screenshot.jpg)

## 系统体检（T1）
- 共 1 条: MAIN_EMPTY
- 详情: [evidence/t1_findings.json](evidence/t1_findings.json)

## 初步归因（执行前请确认）
- 层级: **compiler**（置信度 high）
- 依据: T1 MAIN_EMPTY：有 main 块但 lb-main 为空，优先查编译/role-zone 路由
- 优先文件:
- `learning-buddy-api/app/import_35b/page_compiler.py`
- `learning-buddy-api/app/import_35b/render_zone.py`

## 证据链
1. [images/user_screenshot.jpg](images/user_screenshot.jpg)
2. [evidence/raw_model.json](evidence/raw_model.json)
3. [evidence/blueprint.json](evidence/blueprint.json)
4. [evidence/page.html](evidence/page.html)
5. [analysis/diff_hints.md](analysis/diff_hints.md)

## 入口
- 计划: [EXEC.plan.md](EXEC.plan.md)
- 验收: [commands.sh](commands.sh)
