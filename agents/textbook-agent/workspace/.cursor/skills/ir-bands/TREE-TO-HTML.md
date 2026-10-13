# 从盒子树到 HTML

画页主路径是墨稿（按块框绝对定位，读 `slot`）。下面讲的走树 HTML 是没有页宽时的后备。

`ir_bands.assemble_page` 在内存里产出一页盒子，`ir_bands.build_layout` 仍能转成树（`rows` / `cols` / `flow`），但不写进页 JSON。
读页时服务端 `surfaces/page-ir/paint.mjs` → `render-page.mjs` 的 `renderIrPage` 出 HTML。

公式**只在前端排**：服务端只吐 `data-latex` 空壳，浏览器里由 kb-ui 的 `typesetTextbookMathIdle` 用 MathJax 画。
`<sub>` / `<sup>` 不是数学，摄取时就切成 span 种类（`sub` / `sup`），服务端直接出真标签。

---

# 一条横带

一条横带是一节，占满内容区宽度。

- 两个格子：`display: grid`，列宽跟格子的 `x` 对齐。格子是正文轨（`main`）或侧栏轨（`aside`）。空轨仍占一格，用来对齐，不是树上的「栏缝」节点。
- 通栏：一个孩子占满。真跨两轨的图或通栏正文走这条。

格子里的孩子再套一层同样的规则。

---

# 一个格子

| 节点 | 标签 | 补充 |
|---|---|---|
| 标题 | `h2` 或 `h3` | `data-align`：`center` 或 `left`。只有这两种 |
| 正文 | `p` | `data-indent`：0 / 1 / 2，一级 12 像素 |
| 图 | `figure` | 里面再套面板 |
| 空 | `div.kb-ir-col--empty` | 占住那一轨，高度跟包它的大块一样。只在 region 里出，裸在页顶不出 |

图：

```html
<figure>
  <div class="panel"><img /><figcaption>单张说明</figcaption></div>
  <figcaption class="shared">整行题注</figcaption>
</figure>
```

面板槽名是 `fig_0`、`fig_1`……按面板序号。
题注：`ir/pages` 里已有题注时，空串不覆盖。
成品 HTML 是 `div.kb-ir-page` → `div.kb-ir-region`（`rows`/`cols`）→ `div.kb-ir-col`。
字在 `p` / `h2`；同行两栏中间的制表符渲染成 `span.kb-ir-tab`；
为铺满版面留出来的空白段渲染成 `div.kb-ir-blank`，高度按原空档折成字高。

---

# 一个块只画一次

排版不合并原始块，所以不再有「同一段字既走派生块又单独成段」这条路。`build_tree` 剩两道闸：

- `_caption_srcs`：被吸成图题注的源块，字随图走。注意 MinerU 的 `image_caption` 是图块自己的子框、`src_i` 跟图身相同，得先把图身排除，否则会连图一起剪掉
- `_rescue_missing`：谁都没认领的源块先塞进已经套住它的格子（整片空白的格子会按它的四周切开，不让它撑满一整格），实在没格子才自己成一带。宁可版式糙，也不许内容消失

插槽金样在 `surfaces/page-ir/tests/goldens/`。
