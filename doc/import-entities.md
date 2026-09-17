# 导入实体契约

除 MinerU 固定流程外，导入后处理由知识库 textbook-agent 编排：技能 + 工具 + 任务。前端首次拖入 PDF 仍直连 buddy-api，不经 Agent。

## 六条契约（一个实体算合格）

1. **一份技能** `.cursor/skills/<实体>/SKILL.md`：何时用、边界、禁区。
2. **一到多个工具**：函数名等于文件名；`apply` 分 `scan` / `fix`；无该形状则 0 改动；不写科目名或一书常数。
3. **零到多个任务**：消息统一写成「书号：{{book_id}}。请先读工作区 `.cursor/skills/<技能>/SKILL.md`，再只调用一次 `<工具>(...)`。没有该形状就说一句结束。」
4. **一个事件出口**：经 `_wrap` 发 `tool.<name>`；分科用任务 `when` 机械比对 `meta.json`，不用自然语言条件。
5. **归属进程写明**：Agent 进程（可 `from app import ...`）或自包含跨进程。
6. **登记两处**：`tools/manifest.yaml` 与种子 `agents/textbook-agent/workspace/`（`write_tool` / `write_skill` / `write_surface` 同时写种子）。

## 实体清单

```text
前端拖入 PDF → mineru-ocr（固定流程，前端直连）
                 ├─ kb.import.completed → import-meta
                 └─ kb.import.completed → page-refine
page-refine 完成 → book-toc（build_toc 跑在清洗后的页上）
                 → chinese-layout（when: 语文教材）
toc_extract 完成 → write-toc-pages（非语文 / 课标）
book-toc / chinese-layout → render-audit（缺口：尚无独立任务）
```

### mineru-ocr（识别与转换）

- 首次导入：前端直连 buddy-api，**一行不动**。
- 旁路工具（Agent HTTP）：`mineru_convert` / `mineru_dump` / `mineru_status`。
- 技能：`mineru-ocr`。不配任务。
- 输入：`book_id`，可选页范围。输出：入队结果或本地 `meta.json` / `mineru/manifest.json`。
- 进程：buddy-api 跑 MinerU；Agent 只发 HTTP。

### book-toc（目录，本轮样板）

- 工具：`build_toc`、`toc_extract`、`fill_unit_toc`、`mark_toc_pages`、`write_toc_pages`、`audit_toc`。
- 技能：`build-toc`、`fill-unit-toc`、`write-toc-pages`。
- 任务：`import-book-toc`（`task.import-page-refine.completed`）→ `fill-unit-toc`（语文教材，仍挂 `tool.build_toc`）/ `write-toc-pages`（非语文，`tool.toc_extract`）/ `write-toc-pages-syllabus`（课标，`tool.toc_extract`）→ `write-toc-pages-after-unit`（`tool.fill_unit_toc`，不按 `status:ok` 过滤）。
- 真值：`toc.json` 的 `level` / `is_chapter` / `print_page` / `pdf_page`。目录页唯一写手是 `write_toc_pages`。
- 词法：`toc-lexicon`（`configs/toc_lexicon.yaml`，Python / JS 共读；先剥页码再判类型）。
- 进程：`build_toc` / `fill_unit_toc` 自包含只写 `toc.json`；`write_toc_pages` 只在 Agent 进程经闸门写 `pages/` + `mid/`。缩水超过 60% 拒写（`warning=shrink_guard`），人工 `force=true` 才绕过。

### fig-size（插图原大 / 同行 / 裁切）

- 库（不进 `manifest.yaml`）：`workspace/tools/fig_size.py`、`workspace/tools/figure_recrop.py`。
- 画面：`surfaces/page-body/lib/fig-size.mjs`。技能：`manage-figures`。归属清单见 `doc/fig-code-ownership.md`。
- 宽度在读页时算，不写 `pages/` / `mid/`。裁切在 buddy-worker 导入后上传到原 MinerU 对象键。
- 进程：`fig_size` 只在 textbook-agent；`figure_recrop` 算法自包含，buddy-api 经 `/data/agent/tools/figure_recrop.py` 加载后上传。

### toc-lexicon（目录词法表）

- 配置：种子 `agents/textbook-agent/workspace/configs/toc_lexicon.yaml`，运行时 `data/agent-home/configs/toc_lexicon.yaml`（bind-mount，改完即时生效）。
- 加载器：`workspace/tools/toc_lexicon.py`（mtime 缓存，agent 可改）。`app/toc_lexicon.py` 只是 shim；`learning-buddy-api/app/kb/toc.py` 通过 `/data/agent/tools/toc_lexicon.py` 委托同一份实现。归属清单见 `doc/toc-code-ownership.md`。
- 五段：`page_suffix`（空格页码 / 斜杠页码）→ `lines`（章 / 第N节 / 单元 / …）→ `body`（正文侧定位）→ `column_labels`（栏目停用词，可 `per_book` 覆盖）→ `skip_prefixes`（项目符号前缀，直接丢弃）。
- 正则只用 Python `re` 与 JS `RegExp` 共同子集：编号组，不用命名组。
- 一致性：`tests/test_toc_lexicon.py` 同一批样例分别走 Python 词法与 `surfaces._run_js(..., prepare)`。

### page-refine（逐页清洗）

- 工具：`refine_page`、`strip_sub_sup`、`restore_cid`、`restore_ipa`。
- 技能：`page-refine`。任务：`import-page-refine`。
- 本轮已从清洗消息里删掉第 6 步 `build_toc`，避免目录寄生在长对话里。

### chinese-layout（语文版式）

- 工具：`tag_chinese_layout`、`tag_section_icons`、`tag_drama`、`center_article_heads`、`restore_footnotes`、`continue_fn_series`。
- 技能齐备。任务用 `when: {subject: chinese, category: textbook}`。
- `restore_footnotes` 挂 `task.chinese-textbook-layout.completed`，避免和版式抢同一张图。

### import-meta（元数据判定）— 缺口

- 工具：`propose_import_meta`。任务消息是 `import-page-refine` 的 `identify_message`。
- **缺技能**：科目枚举、册号写阿拉伯数字等规则现在写在任务内联提示里。应抽成 `.cursor/skills/import-meta/SKILL.md`，任务只留「先读技能」。本轮不改。

### render-audit（渲染审计）— 缺口

- 工具：`improve_render`、`write_toc_pages(apply=scan)`、`audit_toc`。
- 技能：`improve-render`。
- **缺独立任务**：现在扫渲染内联在清洗任务第 3、6 步。本轮不改。

## 以后加实体

先在本文补边界、输入输出、事件和进程归属，再写技能 / 工具 / 任务。不要在 buddy-api 里 importlib 执行 Agent 工具。
