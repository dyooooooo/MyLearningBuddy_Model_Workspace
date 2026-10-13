# WorkBuddy 交接目录

本目录是 **WorkBuddy（只读分析方）** 与 **Cursor（唯一改码方）** 之间的交接区。

**协同模式（2026-10-13 起）**：WorkBuddy 只读探查远程仓库、在本地做分析，
产出可执行的施工单放这里；**代码改动一律由 Cursor 执行**。
WorkBuddy 不改动本仓库的任何代码、配置或数据。

---

## 目录内容

| 文件 | 类型 | 状态 | 说明 |
|---|---|---|---|
| `正文细目-施工单-2026-10-13.md` | **施工单** | ★ **待执行** | 结构化库左栏「正文细目」修复。含逐文件 before/after 代码、行号锚点、54 本全库影响面对拍、验收命令、重算步骤 |
| `正文细目-诊断-2026-10-13.md` | 诊断记录 | ⚠️ **勿据此动手** | 施工单的上一版（诊断过程与数据留存）。抬头已标"勿据此动手"，有 2 处设计已被施工单推翻 |
| `kids_fix_proto.py` | 验证脚本 | 可复现工具 | 补丁逻辑原型 + 12 条自检 + 全库影响面扫描 + `--dump` 逐条核对。只读，可直接 stdin 管道到远程执行 |

---

## 怎么用

### 执行修复
读 `正文细目-施工单-2026-10-13.md`。改动清单在第 3 节，重算步骤在第 4 节，验收在第 6 节。

### 自己复现结论（只读，不落任何远程文件）

脚本已经在这个目录里了，**在远程直接跑**即可（`-B` 禁写字节码，跑完不产生任何文件）：

```bash
cd /home/mylearningbuddy/MyLearningBuddy_Model_Workspace

# 自检 + 全库影响面对拍（54 本）
MLB_REPO=$PWD python3 -B doc/workbuddy/kids_fix_proto.py

# 逐条列出某本书「会被删掉」的标题，核对闸门是否误杀
MLB_REPO=$PWD python3 -B doc/workbuddy/kids_fix_proto.py --dump 数学选择性必修
```

已实测：远程跑完 `doc/workbuddy/` 无新增文件、仓库内无新 `__pycache__`。

<details>
<summary>备选：从本地把脚本用 stdin 管道送进去（文件不在本地时用不上）</summary>

```bash
ssh mlb 'cd /home/mylearningbuddy/MyLearningBuddy_Model_Workspace && MLB_REPO=$PWD python3 -B -' \
  < doc/workbuddy/kids_fix_proto.py
```
</details>

---

## 施工单速览

**问题**：结构化库左栏正文细目（`from_body`）里
1. `练习1.1` / `习题1.1` / `A组` / `B组` 被当成小标题
2. `1` / `(1)` / `(2)` / `2` 全被拍成同一级
3. 标题带 `$^{①}$` 脚注、父行标题被重复挂一遍

**根因**：三处全在 `workspace/tools/ir_toc.py::_body_kids()`（871–899 行）

**关键前提**：正文页 IR 块的 `level` 恒为 `2`（MinerU），**层级只能从编号形态推**。

**改动**：5 组文件，其中 3 组是成对播种的双份（`agents/textbook-agent/workspace/{tools,configs}/`
与 `data/agent-home/{tools,configs}/`）——**只改一边会静默失效**，见施工单 §3 开头。

**影响面（真机实测 54 本）**：细目 4758 → 保留 4421 (92.9%)／真删除 **337 (7.1%)**／
新出现 **0**／层级变化 410。删除集中在数学与语文，物理/生物/历史/地理 0 条。
逐条核对数学选必1 被删的 63 条 → **100% 是练习区块，零误杀**。

**改完必须重算**（否则界面不变）：
`ir_toc(book_id, apply="fix", force=True)` 跑 54 本。

---

## 注意事项

- 本目录**只读即可**，不需要 Cursor 在这里写东西。
- 施工单里标 **★** 的是建议先做的确定性改动；标"待裁定"的已用数据消解（见施工单 §7）。
- 仓库里一个多月前的审查文档（`doc/claude_kimi/`、`doc/Codex_Qwen/` 下 2026-09-17 那批）
  **部分条目已被后续改动修掉**。施工单里的每条结论都已在 2026-10-13 回代码核对过，
  例如旧审查说的"`ir_toc.py` 用固定名 `ir/toc.tmp` 会并发撞车"——**已修**
  （现第 1042 行是 `toc.{pid}.{urandom}.tmp` + `os.replace`）。
