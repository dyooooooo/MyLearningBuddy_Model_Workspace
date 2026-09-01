你在清洗一本已入库教材的 Markdown。工作区以外（包括 /app 源码）绝对不能碰。

导入完成、消息里有书号没有单页附件时，按序调用清洗工具：
1. checkpoint（note 写书号与「导入后清洗前」）。
2. restore_cid(书号, apply=fix)：还原 Adobe-Identity 不完整 ToUnicode 的 CID 错位字。无残留则 0 hits，不要手改。
3. restore_ipa(书号, apply=fix)：按书内 Phonetic symbols 表还原斜杠音标字码。无残留则 0 hits。不要按单词另编音标。
4. 对 textbooks/<书号>/pages/ 下每一页调用 refine_page(书号 + 页码)。
5. improve_render(书号) 只扫不改；按 next_steps 再 improve_render(apply=safe) 或 apply=unwrap。agent 类对照原版 PDF 改，不要猜。
6. toc_extract(书号) 再 build_toc(书号)。
7. 再扫一次 improve_render，短说前后条数。不要贴整页正文。

这一轮只处理消息里给出的那一页时：
1. 先调用 refine_page（书号 + 页码），做确定性清洗。
2. 对照附上的原图，核对公式、题号、侧栏、表格和印刷目录页码。
3. 有出入就改 textbooks/<书号>/pages/NNNN.md（官方 Markdown），用临时文件再替换（原子写）。只改了 mid/ 也可以，闸门会把字抽回源；只加围栏不会改源。不要把皮肤 HTML 写进 pages/ 或 mid/。
4. 不要把整页正文贴进对话；短说改了什么即可。
5. 不要开新对话、不要跟前台聊天抢同一条 Cursor 会话。

先读 .cursor/skills/page-refine/SKILL.md。
