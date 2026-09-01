你在清洗一本已入库教材的一页 Markdown。工作区以外（包括 /app 源码）绝对不能碰。

这一轮只处理消息里给出的那一页：
1. 先调用 refine_page（书号 + 页码），做确定性清洗。
2. 对照附上的原图，核对公式、题号、侧栏、表格和印刷目录页码。
3. 有出入就改 textbooks/<书号>/pages/NNNN.md（官方 Markdown），用临时文件再替换（原子写）。只改了 mid/ 也可以，闸门会把字抽回源；只加围栏不会改源。不要把皮肤 HTML 写进 pages/ 或 mid/。
4. 不要把整页正文贴进对话；短说改了什么即可。
5. 不要开新对话、不要跟前台聊天抢同一条 Cursor 会话。

先读 .cursor/skills/page-refine/SKILL.md。
