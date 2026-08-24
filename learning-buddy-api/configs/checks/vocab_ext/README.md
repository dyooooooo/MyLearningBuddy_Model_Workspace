# 词插件（L2b）：受限 Python，热加载，但要走专属批准闸

绝大多数新「眼睛」都能写成 `../vocabulary.yaml` 里的一行表达式。**先试那条路。**
只有当一个原语真的写不成表达式时（例如跨行遍历表格单元格做比对），才写到这里。

## 和声明文件不是一个待遇

`vocabulary.yaml`、`lexicons.yaml`、`*.yaml` 检测器都是**声明**：读进来当数据用。
这个目录里的 `*.py` 是**代码**，会在 buddy-worker 进程里执行。所以：

- 沙箱（`app/services/vocab_plugins.py`）挡越权：禁 `import`、禁下划线属性穿透、
  禁改传进来的对象、禁 `while`（无界循环）、单次求值超时即停、连续超时就停用这个词。
- 沙箱**挡不住「算得不对」**。所以 `EXAMPLES` 是这一层唯一的正确性保证：
  加载期就跑，跑不过这个插件不加载，原因出现在 `GET /api/v1/admin/corpus/checks`
  的 `load_errors` 里。
- 写盘要过**独立的批准闸**（`vocab.write_plugin`），不与 `configs/checks/` 下声明
  文件的批准卡混用。见 `buddy-copilot/agent-env/permissions.yaml`。

## 未过审的草稿放 `proposals/`

加载器只看本目录**顶层**的 `*.py`，不进子目录。所以 `proposals/` 是一个「不会被
执行」的落脚处：草稿先放那里，过审后再移到顶层。

## 文件长这样

```python
NAME = "table_row_has_mixed_units"
SCOPE = "block"                       # block | page
DOC = "表格同一行里混用了不同单位"        # 中文释义，必填
REQUIRES = ["type", "table_html"]     # 只看得见这里声明的字段
EXAMPLES = {
    "positive": [{"type": "table", "table_html": "<tr><td>3 cm</td><td>4 米</td></tr>"}],
    "negative": [{"type": "table", "table_html": "<tr><td>3 cm</td><td>4 cm</td></tr>"}],
}


def evaluate(fields):
    return fields["type"] == "table" and has_cjk(fields["table_html"])
```

`fields` 是只读映射，键**就是** `REQUIRES` 里那些——声明什么才看得见什么，依赖
契约在这里是有牙齿的，不是注释。可直接用的算子和 `vocabulary.yaml` 里一样：
`matches` `contains` `findall` `latex_texts` `has_cjk` `lexicon` `startswith`
`endswith` `len` `min` `max` `abs` `round`，外加 `any` `all` `sum` `sorted` 这些
纯函数式内建。

样例写法和 `vocabulary.yaml` 一致：字符串 = 一个块的正文；映射 = 指定块字段；
`{"blocks": [...], "html": "…"}` = 造一整页（页级词用）。
