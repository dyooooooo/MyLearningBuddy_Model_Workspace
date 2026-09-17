# 教材插图插入代码归属清单

**一句话**：原大宽度、同行排版、裁切清晰度都归 textbook-agent 的
`workspace/`，AI agent 可改（走审批闸）；镜像里只留读页注入与 MinIO 上传。

新增插图逻辑时先查这张表，不要在镜像里再开一份。

---

## 1. 为什么落点是 `workspace/tools/` + `surfaces/page-body/`

不新建平行目录。现有两处已经够用：

| 条件 | 依据 |
|---|---|
| Python 已在 `sys.path` 上，任意 `.py` 可 `import` | `app/workspace_module.py`；只有列进 `tools/manifest.yaml` 的才暴露成 agent 工具，库模块放进去不会变成工具 |
| 已挂进 buddy-api / buddy-worker | `learning-buddy-c2/docker-compose.yml`：`../data/agent-home/tools:/data/agent/tools:ro` |
| 已被审批闸覆盖 | `workspace/.cursor/hooks/guard-redline.py`，agent 写 `tools/*.py` / `surfaces/*` 必须先拿 `fs.request_edit` 批准票 |
| 画面插槽已有金样闸 | `write_surface` → `test_surface` → `reload_surfaces` |

`fig_size.py`、`figure_recrop.py` 与 `equation_display.py` **不进 `manifest.yaml`** —— 它们是库，不是工具。
下次导入会自动按 `figure_recrop.py` 里的 DPI / JPEG 质量重裁；示意图公式按 `equation_display.py` 的形状判定裁图。

---

## 2. agent 可改（`workspace/`，bind-mount）

| 文件 | 职责 |
|---|---|
| `tools/fig_size.py` | **唯一的宽度 / 同行实现**：插图 + 示意图公式（`equation` / `equation_interline`）都按 MinerU 框相对正文栏宽算 `%`。不写 `pages/`、`mid/` |
| `tools/figure_recrop.py` | **唯一的裁切实现**：按 MinerU 框从 PDF 光栅 JPEG（插图 + 示意图公式）。不碰 MinIO、不 `import app.*` |
| `tools/equation_display.py` | **唯一的示意图公式判定**：形状（`xlongequal` / `chemfig` / `array` 夹中文等），不看书号、不看学科 |
| `surfaces/page-body/lib/fig-size.mjs` | Markdown `![]()` → `<img style="width:N%">`，同行包 `kb-fig-row` |
| `surfaces/page-body/theme.css` | `.kb-fig-row` / `.kb-fig-cell` 样式；图本身不再用全局 max-height |
| `surfaces/page-body/prepare.mjs` | 最后一步调用 `sizeImages` |
| `surfaces/page-body/tests/goldens/fig-size.*`、`fig-row.*` | 金样 |
| `.cursor/skills/manage-figures/SKILL.md` | 何时改哪一份 |

开关：环境变量 `TEXTBOOK_FIG_SIZE_BOOKS`（`*` = 全书，空 = 退回旧 CSS 帽）。
Compose 默认 `*`。

---

## 3. 镜像里的薄壳（改行为请改 workspace，不要改这里）

| 文件 | 只做什么 |
|---|---|
| `agents/textbook-agent/app/fig_size.py` | shim，re-export `tools/fig_size.py` |
| `agents/textbook-agent/app/main.py` 读页 | 把本页宽度表 / 同行表传给 `prepare_page` |
| `agents/textbook-agent/app/surfaces.py` | 注入 `ctx.figWidths` / `ctx.figRows`，计入缓存 key |
| `learning-buddy-api/app/kb/workspace_figures.py` | 从 `/data/agent/tools/figure_recrop.py` 加载；宿主机单测回退仓库 seed |
| `learning-buddy-api/app/kb/figure_recrop.py` | 调 `iter_figure_jpegs`，上传到原 MinerU 对象键 |
| `learning-buddy-api/app/kb/pdf_pages.py` | 页预览 JPEG；DPI / 质量优先读 workspace 常量 |
| `learning-buddy-api/app/kb/import_service.py` | `parse_pdf` 之后调一次 `recrop_figures` |

---

## 4. 留在镜像不搬（是管道，不是插图规则）

| 文件 | 为什么 |
|---|---|
| `app/kb/assets.py` | MinIO 上传 |
| `app/kb/workspace_store.py` | 书目录、PDF 路径 |
| `app/surfaces.py` | Node 子进程 runner + golden 闸；渲染规则本身在 `.mjs` |

---

## 5. 三个面怎么保持一致

```text
tools/fig_size.py          宽度%、同行组（Python，读页时算）
    └── ctx.figWidths / ctx.figRows
         └── lib/fig-size.mjs   写成 HTML（JS）

tools/figure_recrop.py     JPEG 字节（Python，导入时裁）
    └── kb/figure_recrop.py    上传，URL 不变
```

宽度**不**写进 `pages/*.md` 或 `mid/*.md`。改算法后刷新 `/kb/` 即新画法；
改 DPI 后要对已导入的书再跑一次导入或 `recrop_book` 才换像素。

---

## 6. 同步与重建

| 改了什么 | 怎么生效 |
|---|---|
| `workspace/tools/fig_size.py`、`figure_recrop.py`、`equation_display.py` | 同步到 `data/agent-home/tools/` 后**重启** textbook-agent（宽度）或 buddy-worker（裁切），不用重建镜像 |
| `workspace/surfaces/page-body/**` | `test_surface` 全绿后 `reload_surfaces`，不用重建 |
| `agents/textbook-agent/app/**` | `docker compose build textbook-agent` 再 `up -d` |
| `learning-buddy-api/app/**` | `docker compose build buddy-api buddy-worker` 再 `up -d` |

验证容器内与宿主机一致：

```bash
docker exec textbook-agent head -1 /data/agent/tools/fig_size.py
```

```bash
docker exec buddy-api head -1 /data/agent/tools/figure_recrop.py
```
