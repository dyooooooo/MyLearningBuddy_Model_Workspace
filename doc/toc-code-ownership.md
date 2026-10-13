# 教材目录代码归属清单

**一句话**：所有「教材目录（TOC, Table of Contents）规则」都归 textbook-agent 的
`workspace/tools/`，AI agent 可改（走审批闸）；镜像里只留传输与编排管道。

新增目录逻辑时先查这张表，不要在镜像里再开一份。

---

## 1. 为什么落点是 `workspace/tools/`

不新建 `lib/`。`tools/` 同时满足三个条件：

| 条件 | 依据 |
|---|---|
| 已在 `sys.path` 上，任意 `.py` 可 `import` | `app/tools_loader.py::_ensure_tools_on_path`；只有列进 `tools/manifest.yaml` 的才暴露成 agent 工具，库模块放进去不会变成工具 |
| 已挂进 buddy-api / buddy-worker | `learning-buddy-c2/docker-compose.yml`：`../data/agent-home/tools:/data/agent/tools:ro` |
| 已被审批闸覆盖 | `workspace/.cursor/hooks/guard-redline.py::_is_tools_py`，agent 写 `tools/*.py` 必须先拿 `fs.request_edit` 批准票 |

先例：`learning-buddy-api/app/kb/workspace_toc.py` 早就用 `importlib` 从
`/data/agent/tools/build_toc.py` 全权加载 agent 代码。

---

## 2. agent 可改（`workspace/`，bind-mount）

| 文件 | 职责 |
|---|---|
| `tools/toc_lexicon.py` | **唯一的 Python 目录词法实现**：剥印刷页码、分类行、层级、栏目停用词、导出给 JS |
| `tools/toc_mineru.py` | 从 `mineru/*content_list*.json` 收目录候选并判强弱（库模块，不进 manifest） |
| `tools/printed_toc_agent.py` | **v2 印刷目录编排**：找页 → 读事实 → 拼装 → 补页码 → 核对，只写 `toc.print` |
| `tools/printed_toc_tools.py` | v2 五个步骤：四样信号找页、逐页文字层/看图、补页码、正文核对、标准答案比对 |
| `tools/printed_toc_layout.py` | 纯函数：按行分栏、合并折行、定层级（不调模型、不写盘） |
| `tools/ir_toc.py` | 中间表示线左栏 `ir/toc.json`：有 `toc.print` 就当骨架（老线做法：正文定页、挂 `from_body` 细目），否则收中间页标题块；`printed_toc_agent` 写完自动调 fix |
| `tools/ir_print_toc.py` | 中间表示线编译：`toc.print` + 目录页标题块 → `ir/print_toc.json`（按目录页分组，跳转页 = 印刷页 + 偏移）；`printed_toc_agent` 写完自动调 |
| `tools/printed_toc_decide.py` | `arch="v2"` 默认路径：每页模型定每行（JSON 结构锁定）→ 事实检查 → 有分歧放大重问 / 三票 → 每条记 `decision` |
| `tools/resolve_printed_toc.py` | 看原图定位/抽骨架，与 MinerU 对照，写 `toc_printed.json`（v1 线，本轮不接 v2） |
| `tools/toc_extract.py` | 小节抽取：有侧车时不再词法重读目录页；`scan_interval_entries` 只扫子层 |
| `tools/toc_text.py` | 目录 HTML（`<nav class="kb-page-toc">`）还原成明文行，与 `tag.mjs::stripSkin` 对齐 |
| `tools/build_toc.py` | 建 `toc.json`：有 `toc_printed.json` 就当骨架，只合并页号与 `from_body` |
| `tools/write_toc_pages.py` | 把 `toc.json` 写回 `pages/*.md` + `mid/*.md`（含缩水/暴涨护栏） |
| `tools/mark_toc_pages.py` | 人工指定目录页并重建 |
| `tools/fill_unit_toc.py` | 单元制教材的目录补全 |
| `tools/audit_toc.py` | 目录巡检 |
| `tools/retag_page.py` | 强制整页重判（目录页围栏丢了时用） |
| `configs/toc_lexicon.yaml` | 共享词法表（数据）：`page_suffix`、`lines`、`body`、`column_labels`、`skip_prefixes`、`per_book` |
| `surfaces/page-body/lib/page-toc.mjs` | 页内目录导航渲染（JS 侧词法，读 `ctx.lexicon`） |
| `surfaces/toc-tree/paint.mjs` | 左栏目录树渲染 |
| `prompts/toc_extract.md` | 抽子层的系统提示词 |
| `prompts/printed_toc_locate.md` · `printed_toc_lines.md` · `printed_toc_review.md` · `printed_toc_fill.md` | v2 印刷目录：缩略图找页 / 整页读行 / 终审并拆分级 / 截图补页码。`printed_toc_read.md` 不接入 |
| `prompts/printed_toc_confirm.md` | 找页复核：只有缩略图一票的页，整页大图六选一（目录 / 版权页 / 前言或说明 / 封面 / 正文 / 其他），答的不是目录就撤票 |
| `prompts/printed_toc_decide.md` · `printed_toc_pair.md` | `arch="v2"`：每页逐行判断 / 两行放大问是否折行 |
| `prompts/toc_locate.md` · `toc_read.md` · `toc_reconcile.md` | v1 印刷目录看图：定位页 / 抽骨架 / 第二遍核对 |
| `.cursor/skills/printed-toc` | v2 印刷目录技能 |
| `tasks/builtin.yaml` | `import-book-toc` / `write-toc-pages` / `fill-unit-toc` 触发 |
| `.cursor/skills/build-toc`、`fill-unit-toc`、`write-toc-pages` | 技能文档 |

`tools/toc_lexicon.py`、`tools/toc_text.py`、`tools/toc_mineru.py` **不进 `manifest.yaml`** —— 它们是库，不是工具。

---

## 3. 镜像里的薄壳（改行为请改 workspace，不要改这里）

| 文件 | 只做什么 |
|---|---|
| `agents/textbook-agent/app/workspace_module.py` | 统一按名字加载 `tools/<name>.py`，走 `sys.modules` 缓存，缺失时回退镜像 seed |
| `agents/textbook-agent/app/toc_lexicon.py` | shim，re-export `tools/toc_lexicon.py` 的公开函数 |
| `agents/textbook-agent/app/toc_extract.py` | shim，re-export `tools/toc_extract.py` 的公开函数 |
| `agents/textbook-agent/app/resolve_printed_toc.py` | shim，re-export `tools/resolve_printed_toc.py` |
| `agents/textbook-agent/app/mid_text.py` | 非目录部分的 skin 拆解；目录部分委托 `tools/toc_text.py` |
| `learning-buddy-api/app/kb/toc.py` | `parse_entry` / `_looks_like_entry` / `looks_like_toc_blob` 委托 `/data/agent/tools/toc_lexicon.py`；加载失败才回退内置正则 |

---

## 4. 留在镜像不搬（是管道，不是目录规则）

| 文件 | 为什么 |
|---|---|
| `app/main.py` 的 `GET /v1/library/{id}/toc`、`POST .../toc-extract`、`POST .../printed-toc` | FastAPI 路由 |
| `app/tasks.py::start_printed_toc` | 独立作业：`resolve_printed_toc` → `build_toc` → `toc_extract`；`import-book-toc` 不再走 chat |
| `app/tasks.py::start_toc_extract` | 作业派发 |
| `app/library.py::read_toc` / `write_toc` | 通用 JSON 读写，无目录规则 |
| `app/surfaces.py` | Node 子进程 runner + golden 闸；渲染规则本身在 `.mjs` |
| `app/page_sync.py` | 文件监听与写盘抑制（`retag_page` 辅助在这里，但判定逻辑在 surfaces） |

---

## 5. 三个语言面怎么保持一致

```text
configs/toc_lexicon.yaml   ← 唯一数据源
    ├── tools/toc_lexicon.py   Python（textbook-agent + buddy-api 共用同一文件）
    └── page-toc.mjs           JS（读 ctx.lexicon，由 surfaces.py 注入）
```

Python 与 JS 语言不同、无法合并成一个文件，靠
`agents/textbook-agent/tests/test_toc_lexicon.py` 拿同一批样例双跑、结果必须一致来锁住。
加词法规则时必须同时补这个测试的样例。

---

## 6. 目录侧车文件

| 文件 | 写 | 读 |
|---|---|---|
| `toc.json` | `build_toc`、`fill_unit_toc`、`library.write_toc` | 所有工具、API、surfaces |
| `toc.print` | `printed_toc_agent` | `ir_print_toc`（编译成 `ir/print_toc.json`）；`ir_toc`（左栏骨架）；标准答案比对走 `toc_golden`。**不接**老线 `build_toc` |
| `ir/print_toc.json` | `ir_print_toc` | page-ir 插槽 `paint.mjs` → `lib/print-toc.mjs`：目录页画成目录列表，样式照老线 `kb-page-toc` |
| `ir/toc.json` | `ir_toc`（fix / review / polish）；`printed_toc_agent` 写完 `toc.print` 自动调 fix | `GET /v1/ir/{书}/toc` → 结构化库左栏。有 `toc.print` 时 `source = ir_toc_print`：印刷目录当骨架，正文标题块定页、挂 `from_body` 细目（老线做法）；否则 `ir_toc_headings` |
| `toc_printed.json` | `resolve_printed_toc` | `build_toc._load_printed_toc`、`toc_extract`（骨架权威） |
| `toc_agent.json` | `toc_extract.write_agent_sidecar` | `build_toc._load_agent_toc` |
| `toc_extract.json` | `toc_extract.save_cursor` | `toc_extract.load_cursor`（区间哈希缓存） |
| `mineru_toc.json` | buddy-worker 导入期 `workspace_store.write_mineru_toc` | `build_toc._load_mineru_toc`（无侧车时的旧路径） |

### 6.0 `toc_printed.json`（印刷目录权威）

骨架（有哪些章/节、印在哪几页）由教材 Agent 看原图决定，写入这本书的 `toc_printed.json`：`toc_pages`、`entries`、`mineru_quality`（`strong` / `weak`）、`winner`（`mineru` / `agent`）、两遍摘要。

- **MinerU 强**：缓存里有 `index` 块，或独立「目录」标题且一行一条、带印张页、标题 ≤ 40。
- **MinerU 弱**：无目录页、节名粘在一段里，或只有零散章名。弱时第一遍看图即权威，不做第二遍。
- 第一遍提示词禁止出现 MinerU 条目；不一致且 MinerU 为强才第二遍核对。
- `build_toc` 有这份侧车就**跳过**对目录页的 `_parse_catalog`。buddy-api 导入期的 `extract_toc` 只是顺手抽一份，不是最终权威。

### 6.1 `toc.json` 条目的 `from_body`

`build_toc._entry` 给 `toc_extract` 从**正文**扒来的小节打 `from_body: true`。

为什么不能用「有没有 `print_page`」代替：`build_toc` 会给正文小节按 offset 反推印张页，
所以两批条目都有页码。`write_toc_pages._prints_on_page` 靠这个标决定哪些不印回目录页
（正文扒来的 level ≥ 2 不印，只进左栏三层）。

### 6.2 区间缓存跟着规则失效

`toc_extract.interval_hash` = 页文本 + `rules_stamp()`（`SCAN_RULES_VERSION` +
`toc_lexicon.lexicon_stamp()`）。

原来只按页文本哈希命中，于是往 `configs/toc_lexicon.yaml` 加停用词、或改
`scan_interval_entries` 的取舍，重跑纹丝不动 —— 看着像「改了没用」，实际压根没重算。
**改了取舍逻辑请把 `SCAN_RULES_VERSION` 加一**；只改 YAML 不用动它（指纹已含 YAML）。

---

### 6.3 `toc.print`（v2 印刷目录，本轮独立）

`printed_toc_agent` 只写这本书的 `toc.print`：`schema_version`、`inputs` 指纹、`pages_meta`、`scores`、`entries`（含 `evidence` / `verified`）、`missing_in_toc`。

- 找页：MinerU `index`、文字层行尾页码、版面服务 `content`、35B 缩略图，只加不减。
- 读事实：优先 PDF 文字层，坐标自检不过才看图。
- **标准答案**在 `agents/textbook-agent/eval/printed_toc/`（git 跟踪），容器以只读方式挂到 `/data/agent/eval`。
- **版面服务** `layout-svc`（容器 `lb-layout`，`127.0.0.1:8504`）跑 PP-DocLayoutV3，只用 CPU。调用方传图，服务不读书库。

`toc.print` 不接老线 `build_toc`（老线冻结）。中间表示这边由 `ir_toc` 照老线 `build_toc` 的做法另写一份：印刷目录当骨架 → 正文比原字定页（`body` / `near`）→ 印刷页 + 偏移（`offset`）→ 没印刷页的看前后行（`search` / `next` / `prev`）→ 每行到下一行之间的正文标题挂成 `from_body`，写 `ir/toc.json`。

## 7. 同步与重建

| 改了什么 | 怎么生效 |
|---|---|
| `workspace/tools/**`、`workspace/configs/**`、`workspace/tasks/**`、`workspace/surfaces/**` | 同步 seed 到 `data/agent-home/` 后即时生效，**不用重建镜像** |
| 上面这些文件**首次**从新镜像播种 | `app/workspace.py::ensure_workspace` 自动做（见 7.1） |
| `agents/textbook-agent/app/**` | `docker compose build textbook-agent && up -d` |
| `learning-buddy-api/app/**` | `docker compose build buddy-api buddy-worker && up -d` |
| `learning-buddy-c2/docker-compose.yml` | `docker compose up -d` |
| `layout-svc/**` | `docker compose build layout-svc && up -d layout-svc` |

验证容器内与宿主机一致：

```bash
docker exec textbook-agent head -1 /data/agent/tools/toc_lexicon.py
```

```bash
docker exec buddy-api head -1 /data/agent/tools/toc_lexicon.py
```

### 7.1 播种只覆盖 agent 没动过的文件

`ensure_workspace` 原来是「文件已存在就不覆盖」，于是运行态 workspace 会永远停在
**第一次启动那版**：升级镜像后 `data/agent-home/tools/*.py` 还是旧代码，跑的和仓库里
不是一回事，很难看出来。

现在按 `data/agent-home/.seed-manifest.json` 记的播种哈希判断：

| 运行态文件状态 | 动作 |
|---|---|
| 不存在 | 播种，记哈希 |
| 与镜像 seed 相同 | 什么都不做，记哈希 |
| 与上次播种的哈希相同（= agent 没改过） | **覆盖成新 seed**，记新哈希 |
| 与上次播种的哈希不同（= agent 改过） | **保留不动**，只记一条 `seed kept` 日志 |

清单还没建立时（本次升级前就存在的 workspace）一律按「agent 改过」处理，所以第一次
升级仍需手工同步一次；之后自动。

### 7.2 改了 `tools/*.py` 要重启 textbook-agent

`tools/` 是 bind-mount 不假，但 `app/tools_loader.py` 把加载过的模块存进
`sys.modules` 且**不按 mtime 重载**。所以「改完保存即生效」只对**本进程还没用过的**
工具成立；这个工具在当前进程里跑过一次之后，同步新文件不管用，跑的还是旧代码。

```bash
docker compose restart textbook-agent
```

这次就栽在这：`write_toc_pages.py` 的修复同步进去了，容器里 `grep` 也确认是新文件，
但重放任务链产出的目录页还是旧样子，因为 07:33 那轮已经把旧模块缓存住了。

### 7.3 目录链要幂等

`build_toc` 读页面文本，`write_toc_pages` 写页面文本 —— 同一份文本进出两次。
任何「重写时丢掉了原文里的信息」都会在**第二次**跑 `build_toc` 时才暴露：
第一次的 `toc.json` 是从原文建的，看着是对的。

回归用例 `tests/test_write_toc_pages.py::test_rewrite_then_rebuild_keeps_chapter_pages`
跑完整来回。改 `_official_line` 的输出格式时必须让它保持绿。

真书上的验证方式是连发三次 `task.import-page-refine.completed`，第 3 轮与第 2 轮
`toc.json` / `toc_agent.json` / 目录页应逐字节相同（第 1→2 轮允许 `skeleton_entries`
多出无页码的「附录」这类条目，那是一次性收敛）。

已经被旧版重写坏的书，页面文本里的信息已经没了，`build_toc` 无从恢复 ——
先用第 8 节的脚本从 MinerU 缓存把目录页还原成原文，再重跑链路。

### 7.4 `sys.path` 顺序

`app/workspace_module.py::ensure_on_path` 先把两个目录都摘掉再按序插，保证运行态
`tools/` **一定**排在镜像 seed 前面。只判断「不在 path 里就插 0 位」不够 —— 运行态目录
若已被别人插过，seed 反而会插到它前面，普通 `import toc_lexicon` 就拿到镜像旧代码。

---

## 8. 修一页而不重跑 OCR

`learning-buddy-api/scripts/repair_kb_page_from_cache.py`：用与导入相同的渲染器
（`blocks_to_page_markdown` + `tag_source`）喂缓存里的 `mineru/blocks.json`，把某几页
重渲回 `pages/` + `mid/`。产物与重导那一页一致。

用途：目录页被旧版 `write_toc_pages` 覆盖（插图丢失、混进正文小节）后就地修回。
全量 `convert` 会先 `reset_derived` 删掉 `mineru/` 再重跑 MinerU 云端 OCR，只想修一页
不该走那条路。

```bash
docker exec -e PYTHONPATH=/app buddy-api python /app/scripts/repair_kb_page_from_cache.py <book_id> 6 7
```

不带 `--apply` 只预览。插图映射按 `kb/pdfs/<book_id>/assets/mineru/<name>` 规则重建，
复用 MinIO 里已有的图，不重新上传。
