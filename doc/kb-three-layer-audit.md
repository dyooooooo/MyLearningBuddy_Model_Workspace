# 知识库三层结构审查

审查日期：2026-09-07 · 全程只读，未做任何写操作。

审查对象：

| 层 | 是什么 | 落盘位置 |
|---|-----|------|
| 第一层 源文件 | 官方页面 Markdown | `data/agent-home/textbooks/<书>/pages/NNNN.md` |
| 第二层 中间文件 | 带 `::: kb-*` 围栏的加工版 | `data/agent-home/textbooks/<书>/mid/NNNN.md` |
| 第三层 渲染显示 | textbook-agent 的 surface 产出 `prepared` HTML，kb-ui 负责展示 | `data/agent-home/surfaces/page-body/` + `exam-ocr-server/kb-ui/` |

取证范围：55 本书、约 5000 页、2.3 GB。

---

## 0. 总体结论

**第一层不是「事实来源」，是一台有损转码器。**

每一次写盘都会无条件把每行 2 个以上连续空格压成 1 个、并删掉行尾空白。这不是针对 `kb-*` 残留的定向清理，而是对全部行生效。结果是这套知识库在物理上无法保存多级列表、代码块缩进和硬换行。

全库实测：

| 指标 | 实测值 |
|---|----|
| 行首带 2 个以上空格的行 | **0 行** |
| 行尾两空格（Markdown 硬换行）的行 | **0 行** |

之前遇到的知识点目录缩进丢失，根因在这里。

另有一个贯穿三层的结构性问题：源文件写盘要过清洗器，中间层由源文件推导，源文件又会被中间层反向覆盖，而判断两边是否一致的 `same_body` 本身也调用同一个有损清洗器。这是一个闭环——任何一层的信息只要清洗器表达不了，就会在下一次同步里被抹平，而且抹平之后三层「看起来是一致的」，检测不出来。

---

## 1. 第一层 · 源文件

### L1-1 【致命】清洗器压掉每一行的缩进与行尾

**位置**：`data/agent-home/tools/source_text.py:78`，函数 `strip_fence_tokens()`

```python
cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
```

这行在逐行循环里，**与该行有没有 `kb-*` 残留无关**。而 `sanitize_source` 是所有写盘路径的必经之地：

- textbook-agent 的 `library.write_page`（`library.py:464`）经 `strip_skin` 调它
- buddy-api 的 `workspace_store.write_page`（`workspace_store.py:204`）直接调它
- 连对齐判定 `same_body`（`page_sync.py:43`）也调它

**实测**（在 `data/agent-home/tools` 下真跑 `sanitize_source`）：

| 输入 | 输出 | 后果 |
|---|---|---|
| `    - [A1a](#a1a)`（4 空格缩进） | ` - [A1a](#a1a)`（1 空格） | 多级列表塌成一级 |
| `行尾两空格硬换行··` | `行尾两空格硬换行` | Markdown 硬换行失效 |
| <code>&#124; 甲   &#124; 乙   &#124;</code> | <code>&#124; 甲 &#124; 乙 &#124;</code> | 表格对齐塌掉 |
| 代码块里 `    if x:` / `        return 1` | ` if x:` / ` return 1` | **Python 语义直接毁掉** |

最后一条最严重：围栏代码块（```` ```python ````）本该是不可触碰区，现在里面的缩进照样被压平。

---

### L1-2 【高】三个容器同写一棵树，只有一个上锁

**位置**：`learning-buddy-c2/docker-compose.yml:282`（textbook-agent）、`:354`（buddy-api）、`:451`（buddy-worker），三者都读写挂载 `../data/agent-home/textbooks`。

| 写入方 | 函数 | 原子写 | 页锁 | 更新 stamp | 触发 sync |
|---|---|---|---|---|---|
| textbook-agent | `library.write_page` | 是（tmp + replace） | 是 | 是 | 是 |
| buddy-api | `workspace_store.write_page:201` | **否** | **否** | **否** | **否** |
| buddy-worker | 同上 | **否** | **否** | **否** | **否** |

buddy-api 侧是裸 `md.write_text()`：

1. 非原子写——读者可能读到半截文件
2. 不更新 stamp——textbook-agent 下一次同步会看到「源脏了」
3. 若此时中间层也脏，就落到中间层优先的分支（见 L2-1），**把 buddy-api 刚写的内容覆盖掉**，无备份无告警

---

## 2. 第二层 · 中间文件

### L2-1 【高】中间层能反向覆盖源文件，冲突时静默判中间层赢

**位置**：`agents/textbook-agent/app/page_sync.py:501-510`

```python
if mid_dirty and not src_dirty:
    _write_source(sid, n, strip_skin(mid))      # 中间层覆盖源文件

mid_newer = _mtime(mid_file) >= _mtime(source_path)
if mid_newer:
    _write_source(sid, n, strip_skin(mid))      # 两边都脏时也覆盖
```

问题有三层：

1. 两边同时被改动时**没有冲突提示**，直接按修改时间裁决
2. 用的是 `>=`，**时间戳打平也判中间层赢**（同秒写入很常见）
3. 被覆盖的源文件内容不备份、不进日志正文，只留一条 `sync.decide action=write_source`

**实测**：`math-hs-kps-comp` 的 mid 文件 mtime 是 `02:46:09`，pages 是 `02:46:08`。中间层永远更新。

---

### L2-2 【高】读一个页面会写盘

**位置**：`agents/textbook-agent/app/main.py:692`，`GET /v1/library/{book_id}/pages/{page_no}/markdown`

| 行号 | 动作 | 写什么 |
|---|---|---|
| 706 | `library.write_page` | 改写源文件 |
| 711 | `library.write_mid` | 改写中间文件 |
| 713 | `sync_page` | 建 mid、写 stamp，可能反向覆盖源文件 |

一个 GET 请求里做三次写。**浏览等于修改**；多个读者并发翻页就是并发写。这也是为什么手写的知识书只是被打开看过，就长出了完整的 mid 与 stamp。

---

### L2-3 【中】非教材的书也被套教材版式，产出纯冗余副本

**位置**：`agents/textbook-agent/app/page_sync.py:479-486`

没有 mid 就跑教材 tagger 生成一份。

**实证**：手写的 `math-hs-kps-comp` 已被生成 `mid/0001.md`–`0006.md` 与 `.stamps/`，stamp 里 `last_origin` 记的是 `watch`（文件监视器触发），而 `diff pages/0002.md mid/0002.md` 的结果是**完全相同**——tagger 一个围栏都没打出来。

| 指标 | 数值 |
|---|---|
| 有 mid 目录的书 | 51 |
| mid 文件数 | 约 5000（与 pages 一一对应） |
| textbooks 总体积 | 2.3 GB |

纯冗余之外，真正的代价是：这一步把 L2-1 的反向覆盖通道接上了。

---

### L2-4 【中】启动时串行扫全库，缺 mid 的页每页起一个 Node 子进程

**位置**：`agents/textbook-agent/app/main.py:92` → `page_watch.scan_all():129`

`page_watch.start()` 启动即触发 `scan_all()`，对每本每页跑 `sync_page`。缺 mid 的页会走 `try_tag` 起 Node 子进程，单次超时 `NODE_TIMEOUT_S = 8`。约 5000 页串行，没有并发也没有分片。

---

### L2-5 【中】防抖抑制用的是全局集合，有竞态

**位置**：`agents/textbook-agent/app/page_sync.py:22`（`_suppress`）· `page_watch.py:107`（`is_suppressed`）

`_suppress` 是模块级 `set[str]`，`suppressing()` 在 `finally` 里 `discard`。两个问题：

1. 两个线程抑制同一路径时，先退出的会把另一个的抑制一起解掉
2. watchdog 事件晚于 `with` 块结束到达时会漏过抑制窗口，触发一轮自己引发的同步

---

## 3. 第三层 · 渲染与显示

### L3-1 【高】种子与现场两份渲染代码已经分叉，且双向同步都断了

**位置**：`data/agent-home/surfaces/page-body`（现场） ↔ `agents/textbook-agent/workspace/surfaces/page-body`（镜像种子）

| 文件 | 状态 |
|---|---|
| `lib/fig-size.mjs` | 现场与种子不同 |
| `lib/page-prose.mjs` | 现场与种子不同 |
| `profile.yaml` | 现场与种子不同 |
| `theme.css` | 现场与种子不同 |
| `probe.mjs` / `probe2.mjs` / `probe3.mjs` | **只在现场存在，且未被 git 跟踪（调试残留）** |
| `dash` / `drama-fences` / `heading-no-example` / `section-icon` 四组 golden | 只在现场存在 |

**为什么会有两份**：容器是 `read_only: true`（`docker-compose.yml:271`），镜像里的 `/app/workspace/surfaces` 不可写；而 surface 的设计目标就是让 agent 在运行时改渲染代码、不重建镜像。可写的那份只能放在 bind-mount 的卷上，于是镜像里那份退化为「出厂默认」。加上 `.last-good`，实际是三份：

```
镜像种子 /app/workspace/surfaces   出厂默认，只读
现场     /data/agent/surfaces      agent 可改，实际被编辑的那份
兜底     surfaces/.last-good       最后一次 golden 全绿的快照，真正被执行的是它
```

三级回退在 `surfaces.py:319` 的 `_pick_source`：现场 golden 全绿 → 拷进 `.last-good` 并从 `.last-good` 跑；不绿 → 用旧的 `.last-good`；`.last-good` 也没有 → 用镜像种子。

**两条同步通路各自断在哪**：

| 方向 | 代码 | 断点 |
|---|---|---|
| 种子 → 现场 | `surfaces.py:100 _copy_if_missing` | 只补缺失文件，**永不更新已存在的**。卷一旦填满，种子后续改动永远推不过去 |
| 现场 → 种子 | `surfaces.py:581 _mirror_surface_seed` | 写的是容器内 `/app/workspace/surfaces`，而 `/` 挂载为 `ro`，写必失败并被 `except OSError: return False` 吞掉 |

实测：`docker exec textbook-agent touch /app/workspace/surfaces/page-body/x` → `Read-only file system`。即使这一步能成功，写的也是容器可写层，重建镜像即丢，回不到仓库。

**后果**：

- 改仓库种子不会生效（要么手工同步到现场，要么删掉现场文件让 `_copy_if_missing` 重新补）
- 现场目录一旦丢失或重置，行为会静默回退到种子版本，那四组 golden 会一起消失
- 两份都在同一个 git 仓库里，但只有人工提交才能保持一致；`probe*.mjs` 至今未被跟踪

---

### L3-2 【中】渲染失败一律静默降级，前台无信号

**位置**：`agents/textbook-agent/app/surfaces.py:538`（`prepare_page`）· `:444`（`tag_page`）· `:319`（`_pick_source`）

```python
except Exception:
    text = raw          # Node 挂了或超时 → 直接返回未渲染的 markdown
```

接口照样返回 200，前端拿到的是没渲染的原文。再叠加「golden 不过就退 last-good / seed」的机制：改了 surface 却没生效时，页面上看不出任何区别，只能翻日志。

---

### L3-3 【中】prepare 缓存无上限，以整页原文为键

**位置**：`agents/textbook-agent/app/surfaces.py:61` · `:540`

```python
_prepare_cache: dict[tuple[str, str, str], str] = {}
# key 的第二项就是整页 markdown 原文；只在 _activate 时 clear
```

没有容量上限，没有淘汰策略。把 5000 页浏览一遍，全部原文加渲染结果常驻进程内存，直到重启或 reload surfaces。

---

### L3-4 【中】公式占位符用 `%%`，且替换会解释特殊模式

**位置**：`exam-ocr-server/kb-ui/src/util/math-render.ts:11` · `:41`

```ts
return `%%MATH_BLOCK_${idx}%%`;                                  // :11
out = out.replace(`%%MATH_BLOCK_${i}%%`, `<div…>${block}</div>`); // :41
```

三个问题：

1. `%%` 不是安全的私有符号，正文里出现同形文本就会撞车
2. `String.replace` 的替换串里 `$&`、`` $` `` 是特殊模式，而这里的替换串是 MathJax 生成的 HTML，含这些字符会被展开成别的东西
3. 四条数学正则跑在整篇文本上，**不避开代码块**，代码里的 `$` 会被当成公式吃掉

---

### L3-5 【中】服务端渲染好的 HTML 又被喂了一遍 Markdown 解析器

**位置**：`exam-ocr-server/kb-ui/src/app.ts:716` · `util/dom.ts:16-25`

```ts
const html = renderMarkdown(out.prepared || out.markdown || "（本页无文本）");
// renderMarkdown 内部：先跑 [[…]] 正则 → 再 marked.parse(…)
```

`prepared` 已经是 HTML，再过一次 `marked.parse`：HTML 里的 `_`、`*`、行首空格都可能被二次解释。`[[…]]` 正则又跑在解析之前，代码块里的 `[[` 一样会被改成链接。

---

### L3-6 【低】HTML 消毒是黑名单而非白名单

**位置**：`exam-ocr-server/kb-ui/src/util/html-sanitize.ts:1`

删标签靠固定列表（`script` / `style` / `iframe` 等），属性只拦 `on*` 与 `javascript:`。`style` 属性、`data:` URL 放行。当前内容源可信，风险不高，但 DOMParser + innerHTML 往返的黑名单消毒是已知的弱模式（mXSS，突变型跨站脚本）。

---

## 4. 如果要修，建议的顺序

### 只修一处

把 `source_text.py:78` 的空格压缩改成**只对含 `kb-*` 残留的行生效**。

这一处解开之后，第一层才具备保存缩进与代码块的能力，其余问题才谈得上修。风险点是 golden 与既有页面的稳定性——**动手之前需要先量化：全库 5000 页里会有多少页输出发生变化**。这一步是纯读操作。

### 修两处

再把 GET 接口里的三次写盘拆出去，读页面不再改盘（`main.py:706` / `711` / `713`）。

L2-1 的反向覆盖、L2-3 的冗余副本、L2-5 的自触发同步，绝大部分是被这条读路径引爆的。

---

## 5. 取证方式

- 直接读代码：`page_sync.py`、`mid_text.py`、`source_text.py`、`surfaces.py`、`main.py`、`page_watch.py`、`library.py`、`workspace_store.py`、`dom.ts`、`math-render.ts`、`html-sanitize.ts`、`app.ts`
- 在 `data/agent-home/tools` 下真跑 `sanitize_source`，对照输入输出
- 对 `data/agent-home/textbooks` 全库 grep 统计缩进行数与硬换行行数
- `diff -rq` 现场 surfaces 与仓库种子
- 检查 `math-hs-kps-comp` 的 mid、stamp、mtime

全程只读，未做任何写操作。
