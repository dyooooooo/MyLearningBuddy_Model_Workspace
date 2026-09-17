---
name: ir-bands
description: 页级竖轨 + 递归切带，写出 ir/pages 的 layout.tree。阈值在 ir/band_rules.yaml。explain 打印一页的轨、带、收口分组。
---

# 嵌套横带（IR 线版式）

工具：`ir_bands.py`（`workspace/tools/ir_bands.py`）。摄取时由 `ingest_layout` → `ir_lib.ingest_page` 调用 `assemble_page` + `build_layout`。

细节：[ALGORITHM.md](ALGORITHM.md) 八步通则与四页对照 · [TREE-TO-HTML.md](TREE-TO-HTML.md) 树怎么变 HTML、一个块为什么只画一次。

命令行排障：

```bash
python3 workspace/tools/ir_bands.py --book <书id> --page 11 --explain
```

打印这一页的版心、正文轨/侧栏轨、每条带的格子成员数、收口大块类型和制表符行数。

## 怎么判

1. 从 MinerU `para_blocks` 铺框，丢掉跨页。
2. 废弃页眉/页码出树；升格的栏目框（想一想等）并进正文框，参与切带。
3. 重叠的图合成图组，矮题注吸附进去。
4. 通栏块横切，其余按列结构切成横带。
5. 整页只投一次栏轨：正文轨＝墨最重 pair 带里最宽的一列；侧栏取另一侧实测缘。
6. 格子里再套：图按面板拆，文字按坐标分行并收口。同行两栏中间是制表符。

不分书、不写死几栏。阈值全部在 `workspace/ir/band_rules.yaml`。

## 哪个阈值管哪种病

| 现象 | 先改 |
|---|---|
| 两列粘成一列 / 一列被拆成两列 | `COL_GAP` |
| 通栏没切断、栏目头被拆开 | `SPAN_FRAC`、`ABSORB_GAP`、`ABSORB_MAX_H` |
| 图段没单独成带 | `WIDE_IMAGE_FRAC`、`MIN_FIG_H` |
| 题注没并进图 | `CAPTION_MAX_H`、`CAPTION_SHARE_FRAC` |
| 侧栏没认出来 / 练习左列被吞进侧栏 | `TRACK_IN_FRAC`、`COL_GAP` |
| 相邻小包该并没并 | `PAIR_GAP`、`TIGHT_PACK` |

改完 YAML 不用重建镜像，下一页 `ingest_layout(..., apply=fix)` 即生效。先 `--explain` 对原图，再重摄取该书。

## 边界

- 零写盘。写 `ir/pages` 的是 `ingest_layout`。
- 不碰 `pages/`、`mid/`、`page_sync`。
- 不要再调用已删除的 `ir_columns`。
