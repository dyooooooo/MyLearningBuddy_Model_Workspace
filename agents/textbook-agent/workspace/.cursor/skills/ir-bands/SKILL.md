---
name: ir-bands
description: 页宽补空 + 盒子生长，给块写 slot。阈值在 ir/band_rules.yaml。explain 打印一页的轨、带、格子成员。树不写盘。
---

# 嵌套横带（IR 线版式）

工具：`ir_bands.py`（`workspace/tools/ir_bands.py`）。摄取时由 `ingest_layout` → `ir_lib.ingest_page` 调用 `assemble_page` + `attach_slots`。页 JSON 不写 `layout.tree` / `tracks` / `em`。画页走墨稿，读块上的 `slot`。

细节：[ALGORITHM.md](ALGORITHM.md) 八步通则与四页对照 · [TREE-TO-HTML.md](TREE-TO-HTML.md) 走树后备怎么变 HTML（墨稿是主路径）。

命令行排障：

```bash
python3 workspace/tools/ir_bands.py --book <书id> --page 11 --explain
```

打印这一页的版心、正文轨/侧栏轨、每条带的格子成员数和制表符行数。

## 怎么判

1. 从 MinerU `para_blocks` 铺框，丢掉跨页。
2. 废弃页眉/页码出树。
3. 重叠的图合成图组，矮题注吸附进去。
4. 画布按整页宽度铺；没占满的地方补空白格。
5. Y 重叠的块分成 N 栏。图段横向长满页宽并切开上下文字；文字和空白纵向长，停在图段处。
6. 盒子套好之后：正文按阅读序和缩进格式化，侧栏不特殊排。图按面板拆。同行两栏中间是制表符。

不分书、不写死几栏。阈值全部在 `workspace/ir/band_rules.yaml`。

## 哪个阈值管哪种病

| 现象 | 先改 |
|---|---|
| 两列粘成一列 / 一列被拆成两列 | `COL_GAP` |
| 通栏没切断、栏目头被拆开 | `SPAN_FRAC`、`ABSORB_GAP`、`ABSORB_MAX_H` |
| 图段没单独成带 | `WIDE_IMAGE_FRAC`、`MIN_FIG_H` |
| 题注没并进图 | `CAPTION_MAX_H`、`CAPTION_SHARE_FRAC` |
| 侧栏没认出来 / 练习左列被吞进侧栏 | `TRACK_IN_FRAC`、`COL_GAP` |
| 上下文字该并没并 / 被拆开 | `PAIR_GAP`、`TIGHT_PACK`、`SIG_RATIO_TOL` |
| 左右栏往上跑、空白对不上 | `SIG_RATIO_TOL`（结构或比例不同就不要并） |

改完 YAML 不用重建镜像，下一页 `ingest_layout(..., apply=fix)` 即生效。先 `--explain` 对原图，再重摄取该书。

## 边界

- 零写盘。写 `ir/pages` 的是 `ingest_layout`。
- 不碰 `pages/`、`mid/`、`page_sync`。
- 不要再调用已删除的 `ir_columns`。
