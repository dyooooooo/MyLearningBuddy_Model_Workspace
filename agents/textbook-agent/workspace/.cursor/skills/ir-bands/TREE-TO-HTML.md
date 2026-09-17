# 从盒子树到 HTML

`ir_bands.assemble_page` 产出一页盒子树，`ir_bands.build_layout` 把它转成 `layout.tree`（`rows` / `cols` / `flow`），
写进 `ir/pages/NNNN.json`。读页时服务端 `surfaces/page-ir/paint.mjs` → `render-page.mjs` 的 `renderIrPage` 出 HTML。

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
| 空 | `div.kb-ir-col--empty` | 占住那一轨，不写字。只在 region 里出，裸在页顶不出 |

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
收口后的字在 `p` / `h2`；同行两栏中间的制表符渲染成 `span.kb-ir-tab`。

---

# 一个块只画一次

收口块（`kb-close-*`）是派生物，`source_ids` 记着它并了哪些源块。同一段字既走收口块又单独成段就会出两遍，
所以 `build_tree` 有三道闸：

- `eaten`：被收口行吃掉的源块，不再单独渲染
- `_caption_srcs`：被吸成图题注的源块，字随图走。注意 MinerU 的 `image_caption` 是图块自己的子框、`src_i` 跟图身相同，得先把图身排除，否则会连图一起剪掉
- `_rescue_missing`：谁都没认领的源块自己成一带，按原位插回去。宁可版式糙，也不许内容消失

插槽金样在 `surfaces/page-ir/tests/goldens/`。
