---
name: manage-self
description: 改自己工作区里的书、轨迹、技能、规则、提示词、钩子、插件或 Python 工具。
---

# 改自己的工作区

对工作区整棵目录拥有读、写、删、改的全部权限：`textbooks/`、`traces/`、`.cursor/`、`tools/`、`surfaces/`、`prompts/`、`memory/`。

没放进工作区的（尤其是 `/app` 里的导入服务、网关、语料台）没有权限，钩子会直接拒绝。

| 要改的事 | 改哪里 |
|----------|--------|
| 三级目录、印张页 | 全书共性形状才改 `tools/build_toc.py`；一书特例只改该书 `pages/`（官方 MD）和 `mid/`（围栏），见 `page-layers` |
| 左侧目录怎么画 | `surfaces/toc-tree/` |
| 书页正文怎么渲染 | `surfaces/page-body/` |
| 插图原大、同行、清晰度 | `tools/fig_size.py`、`tools/figure_recrop.py`、`surfaces/page-body/lib/fig-size.mjs`，见 `manage-figures` |
| 新功能 | 新建工具 + `create_task(mode=trigger)`，不要给已有工具打补丁 |

除非用户本轮明文要求改代码、添加工具或加钩子，否则不要改 `tools/`、`surfaces/`、钩子、技能。共用工具/渲染只加所有书都适用的形状。一书专名不准写进正则。改 `tools/*.py`、钩子或 `surfaces/*` 须先 `request_code_edit` 并等用户在授权卡上点授权（见 `.cursor/rules/open-closed.mdc`）。

红线（不要碰，钩子会拦）：
- `.cursor/projects/` — 会话存储，删了对话就显示不出来
- `.checkpoints.git/` — 回退能力本身
- `/app` — 服务镜像源码，不是工作区

画面怎么画（目录标题、公式保护、左侧目录树、对照图例）走 `manage-surfaces`：改 `surfaces/<插槽>/` 并跑金样，不要改 Markdown 去「修」渲染 bug。

工作区以外（`/app`、系统源码、其它磁盘）绝对不能碰。

1. 先 `list_config` 看现有技能、规则、工具、提示词、钩子、插件。
2. 加 Python 工具：
   - 文件放在 `tools/` 下，例如 `tools/foo.py`
   - **函数名必须等于工具编号**，例如 `def foo(query: str) -> dict:`
   - 调用 `write_tool`：`tool_id=foo`，`path=foo.py`，`content` 为完整源码，`description` 写人话
   - `write_tool` 会登记到 `tools/manifest.yaml`
3. 加技能：`write_skill`，正文是 `SKILL.md`（含 YAML 头）。
4. 加规则：`write_rule`，正文是 `.mdc`。
5. 改系统提示词：`write_prompt`，`name` 为 `chat` / `plan` / `ask` / `grade` / `title`。
6. 钩子：`write_hook_script` 写 `.cursor/hooks/` 脚本，再 `write_hook` 登记到事件。
7. 打点 / 回退：`checkpoint("说明")`，`list_checkpoints`，`restore_checkpoint(ref)`。
8. 写完立刻 `reload_config`，下一轮才会用上新工具。

不要编造教材内容。没有书就说没有。

用户说「继续」或消息里有「【断点续传】」或「【工作笔记】」时：按笔记和断点接着做。收工可把以后都成立的短句追加到 `memory/DECISIONS.md`。不要打开 `traces/`、`.cursor/projects/*/agent-transcripts`，也不要翻更早几轮对话来猜意图。
