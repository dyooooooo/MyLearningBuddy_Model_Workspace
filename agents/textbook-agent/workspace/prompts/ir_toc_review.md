你在审核一本书的目录草稿。每一项都已经打开了中间页：`page_titles` 就是该 pdf 页上的标题块，按阅读顺序。不要再去猜别的页。

对 items 里的每一项都要给出决定，一项都不能漏。

- drop：练习、习题、例题、A组、B组、思考、探究里的操作指令，以及 “Answer the questions” “Work in groups” 这种练习指令。页眉重复、单独一个页码，也 drop。
- rename：字写错了，或层级不对。label 写改后的标题。level 用 0 表示章或单元，1 表示节，2 表示节下面的小标题。
- keep：这就是该留在目录里的标题，层级也不用改。

只输出一个 JSON 对象，不要解释。形状如下：

{"items":[{"pdf_page":9,"title":"原来的标题","action":"keep"}]}

title 必须和输入的 title 一致，pdf_page 必须和输入的一致。action 只能是 keep、drop、rename。
