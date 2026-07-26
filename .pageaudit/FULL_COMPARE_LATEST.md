# 35B 最新落盘逐页全量对比报告

**Job MinIO**: `7b33d74bc3d042a0b9836116f8cc3bdf`  
**Job DB**: `de6bd85c-8eef-4126-8953-cfadd80abb55`  
**时间**: 2026-07-03 · **prompt**: v5 · **repair**: 已删除  
**数据源**: MinIO `import35b/.../assets/35b/debug/p*/`（raw_model / blueprint / page.html / debug.json）

> 非 `.pageaudit/pages.json`（旧 job `f71290dc…`）。

---

## 对比方法

| 层 | 文件 | 检查项 |
|----|------|--------|
| 模型 | `raw_model.json` | kind 分布、空 text、截断 |
| 结构化 | `blueprint.json` | 同 raw + role/zone、equation、figure caption |
| 渲染 | `page.html` | 与 blueprint 块覆盖、`<p>`/figure/table、LaTeX |
| 流水线 | `debug.json` | finish_reason、truncated、attempt_errors |

**raw → blueprint**：30 页 **kind 分布全部一致**（MFR/crop/normalize 未改 block 类型）。

**blueprint → HTML**：块 ID 覆盖率 29/30 页 = 100%（仅 p29 空白页例外）。

---

## 错误分类汇总

| 类别 | 严重度 | 影响页 |
|------|--------|--------|
| A. 版式/role 错（正文进 running header，main 空） | 🔴 | **3** |
| B. LaTeX 中 `<`/`>` 被 HTML 转义为 `&lt;`/`&gt;`（MathJax 无法解析） | 🔴 | **12**(12处), 13(3), 18(2), 19(3), 20(1), 21(3), 23(2), 24(3), 25(6), 26(2), 27(2), 28(3) |
| C. `cases` 环境渲染失败 → fallback 纯文本 | 🔴 | **14**(1处) |
| D. 空白页 | 🟡 | **29** |
| E. bbox 校验失败重试（第二次成功） | 🟡 | **25** |
| F. 裸 Unicode 数学符号（未包 `$`） | 🟡 | **3** p3_b5 |

**未发现**（本次 run）：空 text 块、p20/p27 全文变 figure、JSON 截断、p12/p14 正文丢失。

---

## 30 页总表

| pg | 状态 | blocks | kinds | `<p>` | inline | ent⚠ | fb⚠ | 结论 |
|----|------|--------|-------|-------|--------|------|-----|------|
| 1 | ok | 7 | text×6 fig×1 | 0 | 0 | 0 | 0 | 封面 figure，正常 |
| 2 | ok | 6 | run×1 text×4 fig×1 | 5 | 0 | 0 | 0 | ✅ |
| 3 | ok | 6 | text×5 table×1 | 0 | 0 | 0 | 0 | 🔴 版式错误 |
| 4 | ok | 6 | run×2 text×4 | 4 | 0 | 0 | 0 | ✅ |
| 5 | ok | 6 | run×2 text×4 | 4 | 0 | 0 | 0 | ✅ |
| 6 | ok | 3 | run×2 text×1 | 19 | 0 | 0 | 0 | ✅ 目录 toc |
| 7 | ok | 4 | run×2 text×2 | 10 | 0 | 0 | 0 | ✅ |
| 8 | ok | 3 | fig×1 text×2 | 4 | 0 | 0 | 0 | ✅ |
| 9 | ok | 16 | run×2 fig×1 text×13 | 19 | 31 | 0 | 0 | ✅ |
| 10 | ok | 9 | run×2 text×6 table×1 | 18 | 12 | 0 | 0 | ✅ |
| 11 | ok | 12 | run×2 text×10 | 25 | 27 | 0 | 0 | ✅ |
| 12 | ok | 14 | run×2 text×9 eq×1 fig×2 | 18 | 43 | **12** | 0 | 🟡 正文全，LaTeX 转义 |
| 13 | ok | 7 | run×2 text×4 eq×1 | 26 | 36 | 3 | 0 | 🟡 LaTeX 转义 |
| 14 | ok | 22 | run×2 text×17 fig×1 eq×2 | 24 | 62 | 0 | **1** | 🟡 正文全，cases fallback |
| 15 | ok | 7 | run×2 text×4 eq×1 | 22 | 63 | 0 | 0 | ✅ |
| 16 | ok | 14 | run×2 text×10 fig×2 | 10 | 37 | 0 | 0 | ✅ |
| 17 | ok | 14 | run×2 text×11 fig×1 | 12 | 53 | 0 | 0 | ✅ |
| 18 | ok | 8 | run×2 text×5 fig×1 | 29 | 39 | 2 | 0 | 🟡 LaTeX 转义 |
| 19 | ok | 14 | run×2 text×12 | 16 | 51 | 3 | 0 | 🟡 LaTeX 转义 |
| 20 | ok | 12 | run×2 text×10 | 16 | 12 | 1 | 0 | ✅ 全 text，无 figure 化 |
| 21 | ok | 12 | run×2 text×9 eq×1 | 23 | 39 | 3 | 0 | 🟡 LaTeX 转义 |
| 22 | ok | 7 | run×2 text×5 | 19 | 42 | 0 | 0 | ✅ |
| 23 | ok | 7 | run×2 text×5 | 19 | 80 | 2 | 0 | 🟡 LaTeX 转义 |
| 24 | ok | 19 | run×2 fig×2 text×13 eq×1 table×1 | 13 | 29 | 3 | 0 | 🟡 LaTeX 转义 |
| 25 | ok | 29 | run×2 text×26 eq×1 | 26 | 61 | 6 | 0 | 🟡 LaTeX 转义 + bbox 重试 |
| 26 | ok | 12 | run×2 text×10 | 22 | 85 | 2 | 0 | 🟡 LaTeX 转义 |
| 27 | ok | 4 | run×2 text×2 | 26 | 48 | 2 | 0 | ✅ 内容提要/复习题在 text |
| 28 | ok | 20 | run×2 text×18 | 29 | 90 | 3 | 0 | 🟡 LaTeX 转义 |
| 29 | blank | 1 | text×1 | 0 | 0 | 0 | 0 | 🟡 PDF 空白检测 |
| 30 | ok | 4 | fig×1 text×3 | 4 | 0 | 0 | 0 | ✅ 章首页 |

`ent` = data-latex 中含 `&lt;`/`&gt;` 的公式数；`fb` = MathJax fallback 数。

---

## 有问题页面详解

### p03 🔴 版式/结构（唯一严重结构错误）

**原 PDF**：版权页（编委会表格 + 出版信息 + 版权声明）。

**blueprint**（6 blocks，全部 `role=running_header`，`zone=main`）：
- p3_b1–b6：text/table 内容完整（619 字）
- p3_b4：table 10 行出版信息

**HTML**：
- 全部塞进 `<header class="lb-running-hd">`（~500 字）
- `<section class="lb-main">` **为空**
- **无** `lb-table-wrap`（表格未按 table 渲染）

**根因**：模型把版权页所有 block 标成 `running_header`，compiler 按 role 输出到页眉，main 区空白。

---

### p12 🟡 正文完整，LaTeX 转义 12 处

**内容**：例 4、区间定义、图 1-1-1/1-1-2 — **9 个 text 块全有内容**，18 个 `<p>`，2 figure。

**错误**：`page_compiler` 把 `$...$` 写入 HTML 属性时对 `<`/`>` 做了 HTML escape：

```
data-latex="x &lt; 0"          ← 应为 x < 0
data-latex="C=\{(x,y) \mid xy&lt;0\}"
data-latex="a \leqslant x&lt;b"
...共 12 处
```

MathJax 收到 `&lt;` 无法当小于号解析 → **区间符号页公式不显示**。

**raw = blueprint**：一致，非后处理丢失。

---

### p14 🟡 正文完整，1 处 cases fallback

**内容**：17 text + 1 figure(图1-1-3) + 2 display equation，24 `<p>`。

**display 公式**（blueprint 正常）：
- `N ⊂ Z ⊂ Q ⊂ R`
- `\begin{cases} 2x=4, \\ x+y=8 \end{cases} 或 ...`

**HTML 错误**（p14_b8 段内）：
- 成功：`\begin{cases} x=2, \\ y=6 \end{cases}`
- **失败 fallback**：`<span class="math math--fallback">cases x=4, \ y=0.</span>`
  - 模型在 **inline text** 里写了第二组 cases，normalize/compiler 未识别 `\begin{cases}` → 降级为纯文本

---

### p25 🟡 bbox 重试

- 第 1 次：`blocks.27.bbox=[120,980,910,1005]` 超出 0..1000 归一化范围 → Pydantic 拒绝
- 第 2 次：成功，29 blocks 全部入库
- 另有 6 处 LaTeX `&lt;`/`&gt;` 转义

---

### p29 🟡 空白页

- `status=blank_detected`，无 raw_model
- blueprint 仅 `p0029_blank` 空 text 块
- 符合 PDF 空白检测预期

---

### p13, p18–p28 🟡 LaTeX HTML 转义（除上述外）

同上：`data-latex` 属性中的 `&lt;`/`&gt;` 导致含不等号的 inline 公式可能不渲染。`\leqslant`、`\varnothing` 等 **无转义问题的公式正常**。

---

## 无问题页面（18 页）

2, 4, 5, 6, 7, 8, 9, 10, 11, **15, 16, 17, 20, 21, 22, 27, 30** — 结构、正文、figure/text 分类均正常；20/27 确认 **非 figure 化**。

---

## 与旧 job（f71290dc）差异（说明上次误判）

| 页 | 旧 job | 新 job |
|----|--------|--------|
| 12 | 20+ text:null，HTML 仅 4 figure | 9 text 全满 + 2 figure |
| 20 | 11 figure + 长 caption | 10 text，0 figure |
| 27 | 7 figure，提要进 caption | 2 text block，26 `<p>` |

---

## 修复优先级（根因级，非 case-by-case repair）

1. **page_compiler**：`data-latex` 属性写入前不对 `<>&` 做 HTML entity 转义（或对 LaTeX 段单独 escape）
2. **prompt/schema**：版权页/table 页禁止 `role=running_header` 滥用（p3）
3. **normalize**：inline text 中的 `\begin{cases}...\end{cases}` 识别与拆分（p14）
4. **guided JSON**：bbox 硬约束或 clamp（p25 重试）

机器可读明细：`.pageaudit/full_audit.json`、`.pageaudit/latest_job_compare.json`
