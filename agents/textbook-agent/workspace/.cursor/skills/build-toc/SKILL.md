---
name: build-toc
description: 按印刷目录骨架生成三级目录（章/节/小节）并写入 toc.json。
---

# 生成三级目录

目录生成的实现是工作区 `tools/build_toc.py` 与 `tools/resolve_printed_toc.py`。导入写完 `pages/*.md` 后由任务 `import-book-toc` 用独立作业 `printed_toc` 跑完，不要在对话里再开一座看图桥。只有确认是全书共性的形状才能改这些文件；一书专名不准写进来。不要碰 `/app`。改 `.py` 须先 `request_code_edit` 并等用户在授权卡上点授权。

1. `list_config` 确认已有 `resolve_printed_toc`、`build_toc`、`toc_extract`。
2. **不要**在聊天任务里调 `resolve_printed_toc`。看原图由独立作业完成，写入 `toc_printed.json`。
3. 有侧车之后 `build_toc` 用它当骨架：
   - 不再对目录页跑 `_parse_catalog`
   - 只做印张页 ↔ PDF 页对齐、正文回补引论/第N部分/附录、合并 `from_body` 子层
   - 没有侧车的旧书仍走原来的目录页词法
4. 接着 `toc_extract` 只扫正文子层。写目录页挂在 `import-book-toc` **完成**上。
5. 不要手填没有书页依据的条目。不要把某一册的单元名写进工具。
6. `from_body` 子层只进左栏，不印回目录页。
