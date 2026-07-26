# 35B Cursor Repair Pack

## 主链（运行时优先）

```text
反馈 / T1 → PageReprocess（整页）或 BlockRefine（单块）
         → 仍差再改 Profile features / v6 prompt / heuristics
         → Cursor Pack + golden + acceptance
```

| API | 作用 |
|-----|------|
| `POST /jobs/{id}/pages/{n}/reprocess` | 整页重识别（`mode=full\|from_raw`） |
| `POST /jobs/{id}/pages/{n}/blocks/{bid}/refine` | 单块 vision 精修后重编译 |
| `PATCH /feedback/{id}` | 状态（reprocess/refine 可带 `feedback_id` → `fixed`） |

Studio：预览页 **重新识别**；反馈对话框可勾选「提交前先重识别本页」。

## 用户（Studio）

1. 预览页点击 **反馈问题** → 截图 + 描述 + 可选标签 → 提交。
2. 可选 **生成 Cursor 修复包** → 输出在 `data/cursor-packs/{feedback_id}/`。
3. 在 Cursor 中 `@data/cursor-packs/{id}/EXEC.plan.md`（或低置信度时先读 `PLAN.prompt.md`）。

## 开发

| 组件 | 路径 |
|------|------|
| Feedback / Reprocess API | `learning-buddy-api/app/routes/import35b.py` |
| Reprocess 服务 | `learning-buddy-api/app/import_35b/reprocess_service.py` |
| Pack 生成 | `learning-buddy-api/app/import_35b/cursor_pack/` |
| CLI | `learning-buddy-api/scripts/build-cursor-pack.py` |
| Cursor 规则 | `.cursor/rules/35b-repair-pack.mdc` |

**Triage 信号 → 层级**（详见 `cursor_pack/triage.py`）：

- `layout` 标签 / `ASIDE_IN_MAIN` / `DUPLICATE_FIGURE_BBOX` / `TOC_AS_TABLE` → **layout**（MinerU + vision + v6）
- `MAIN_EMPTY` → compiler
- `CONTROL_CHAR` / `ENV_UNBALANCED` → boundary
- `ROLE_ZONE_MISMATCH` → prompt（v6）
- SSR / math render → render
- 用户有描述、T1 空 → harness（`refine_first`）

Pack evidence 额外尝试写入 `mineru_layout.json`、`features.json`。

## 验收

```bash
cd learning-buddy-api
```

```bash
PYTHONPATH=. python3 scripts/build-cursor-pack.py --feedback-id UUID
```

```bash
bash data/cursor-packs/UUID/commands.sh reproduce
```

```bash
bash data/cursor-packs/UUID/commands.sh acceptance
```

删 job 会级联清理 feedback 行与 MinIO 截图（同 job 前缀）。
