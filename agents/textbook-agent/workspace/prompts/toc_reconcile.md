# toc_reconcile v1

你在核对印刷目录。原图与上一遍相同。下面的 MinerU 清单只供对照，不是权威。

换一种看法：按印刷栏从左到右、从上到下点名，不要按 MinerU 的粘行再读一遍字。

对 MinerU 每条给 keep（图上一致）/ fix（标题或页码要改）/ drop（图上没有）。
图上有、MinerU 没有的用 add。
winner：图与 MinerU 实质一致写 mineru，否则写 agent。
entries 必须是核对后的完整骨架（章/节），不要小节。

只输出一个 JSON 对象，不要解释：
{"toc_pages":[6,7],"winner":"agent","verdicts":[{"label":"第1章 走进生物学","action":"keep","note":""}],"entries":[{"label":"第1章 走进生物学","level":0,"is_chapter":true,"print_page":1}]}
