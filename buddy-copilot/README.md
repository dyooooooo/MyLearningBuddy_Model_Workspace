# Buddy Copilot — 宿主机编排服务（:8200）

用 Cursor SDK（Python `cursor-sdk`）接管 MinerU 全书导入与会话内定点改页
（场景 `import_advice`；事后聚焦某页在同一会话完成，不再另开页级分诊）。

**v2 = Cursor 式自主执行**：行动简报确认后，Agent 在一次运行内自主调工具、
自诊断、自修复、重试直到完成；发布方案 / rebuild 容器 / 改文件三类闸门动作
永远先弹批准卡，用户点了才执行。

必须跑在宿主机：Agent 要改工作区代码并 `docker compose build`（容器内做不到）。

## 启动（推荐：systemd 用户服务）

`:8200` 应由 **systemd --user** 常驻，不要长期用前台 `./run.sh`。

首次准备：

```bash
cd buddy-copilot
```

```bash
python3 -m venv .venv
```

```bash
./.venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env   # 填 CURSOR_API_KEY
```

安装并启动服务：

```bash
./deploy/install-user-service.sh
```

常用：

```bash
systemctl --user status buddy-copilot
```

```bash
systemctl --user restart buddy-copilot
```

```bash
journalctl --user -u buddy-copilot -f
```

开机自启（注销后仍保持）：

```bash
sudo loginctl enable-linger "$USER"
```

临时前台调试仍可用 `./run.sh`（先 `systemctl --user stop buddy-copilot`）。

健康检查：`curl -s http://127.0.0.1:8200/api/v1/copilot/health`

## 结构

| 路径 | 职责 |
|---|---|
| `app/main.py` | FastAPI：会话/消息/SSE 事件流/批准卡（支持「本会话总是允许」）/附件/审计 |
| `app/loop.py` | 自主运行监督器：简报确认 → 连续运行 → 最终报告 → fs_guard 终检 |
| `app/agent_runner.py` | cursor-sdk 封装（本地运行时、MCP 注入、最终报告提取、取消） |
| `app/agent_env.py` | 上下文包装配（规则+资源地图+pipeline 地图+技能+任务数据） |
| `app/permissions.py` | `permissions.yaml` 加载（分档/闸门/场景可见性/白名单/预算） |
| `app/mcp_server.py` | MCP（stdio）通用装配器：注册表挂载 + gate/预算/占位符/step 中间件 |
| `app/tools/registry.py` | 工具插件注册表（ToolSpec / ToolContext） |
| `app/tools/plugins/` | 工具插件：editions / corpus / harness / system / session / **ip_tools**（整理·拆分） |
| `app/scenarios/` | 场景：`import_advice` / `pattern_abstract` / **`ip_triage`** / **`ip_split`** |
| `agent-env/` | 资源地图、规则、技能、pipeline 地图、`permissions.yaml`、`pinning.yaml` |
| `scripts/check-agent-env.sh` | 一致性守护：必选文件 / 路径漂移 / 工具三方核对 |

## OCP 扩展点（对修改封闭、对扩展开放）

- **新增工具** = `app/tools/plugins/` 加一个 ToolSpec + `permissions.yaml` 加一行；
  loop / mcp_server 零改动
- **权限即数据**：分档、闸门、文件白名单、预算、场景级工具可见性全部在
  `agent-env/permissions.yaml`；核心代码无工具名/路径硬编码
- **新增场景** = `app/scenarios/` 注册一个 Scenario + permissions.yaml 声明可见工具子集
- **批准卡元数据渲染**：标题/说明/风险由后端下发，前端 CopilotPanel 通用渲染

## Agent 环境工程

- **资源地图**：`agent-env/resources.yaml`（what/where/how/mutability）
- **Pipeline 地图**：`agent-env/context/{import-pipeline,editions,reprocess}.md`
- **Rules / Skills**：`agent-env/rules/`、`agent-env/skills/`，每次会话显式注入，`setting_sources=[]`
- **MCP**：`Agent.create` 注入 stdio 服务器 `buddy-copilot`（`python -m app.mcp_server`），
  会话 id / 场景经 `COPILOT_SESSION_ID` / `COPILOT_SESSION_KIND` 环境变量注入
- **钉扎**：`agent-env/pinning.yaml`（cwd / setting_sources / MCP）；模型默认 `auto`（服务端选），
  前端会话面板可从 `GET /api/v1/copilot/models` 列表中另选，会话创建时生效

## 权限四档（`agent-env/permissions.yaml` 为唯一权威）

| 档位 | 行为 | 工具 |
|---|---|---|
| 只读 | 自动 | editions 查询 / corpus 查询 / minio.stat / golden / page_diff / 资源地图 |
| 运行 | 自动（不改代码） | corpus.start_import / corpus.reprocess_page / harness.run_pytest / session.update_todos |
| 草稿 | 自动（Postgres 草稿可回滚） | editions.save_draft / editions.fork |
| 闸门 | 批准卡阻塞等待 | editions.publish / fs.request_edit / docker.rebuild |

## 安全边界

- 绑 `0.0.0.0:8200`（供 c2-gateway 容器经 host.docker.internal 访问），除 health 外
  全部要求 Bearer ADMIN_TOKEN；Studio 只经 c2-gateway `/api/v1/studio/copilot/*` 代理访问
- 改文件必须先 `fs.request_edit` 获批；会话结束 fs_guard 终检，
  白名单外改动 / 未获批改动全部按文件回滚并报告
- 文件白名单：`configs/editions/**`、`data/prompts/**`、`app/import_35b/prompts/**`、`tests/**`
- 预算护栏：单轮运行超时、批准等待超时（超时=拒绝）、每会话工具调用上限、
  最终报告追问上限——均在 `permissions.yaml` `budgets` 段
