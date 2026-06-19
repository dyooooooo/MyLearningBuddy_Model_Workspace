# DGX Spark AI Lab — Agent Guide

## Project Purpose

**Product goal**: [**Learning Buddy**](doc/learning-buddy-status-and-roadmap.md) — 伴学型教学记忆智能体（⑥→C2→A/B→A⁺→①–⑤ 闭环）。当前里程碑 **M0**；近期 **M1**（切题、错题、A⁺ 初版）。

**Platform**: Configure **NVIDIA DGX Spark** as the AI lab for Learning Buddy:

- **C1 / models**: inference, fine-tune, serve (NGC, vLLM, NeMo)
- **③ agents**: LangGraph orchestration R&D (production ③ lives in `learning-buddy-api`, M3+)
- **R&D**: build.nvidia.com/spark playbooks, Cursor agents, MCP

**Related repos** (not on this machine): `exam-ocr` (C2, WSL), `exam-ocr-server` (C2 gateway), `MyLearningBuddy` (Android ⑥⑤)

## Machine

| Item | Value |
|------|-------|
| Host | gx10-f72e |
| GPU | NVIDIA GB10 (Grace Blackwell, 128 GB UMA) |
| OS | DGX OS 7.5.0 / Ubuntu 24.04 |
| CUDA | 13.0 |
| IP | 192.168.3.200 |

## Cursor Environment

### Rules (`.cursor/rules/`)

| Rule | Purpose |
|------|---------|
| `learning-buddy.mdc` | **Product mission** — 伴学架构、仓库边界、M0–M7 |
| `project-mission.mdc` | DGX Spark lab + NVIDIA-first priorities |
| `context7-auto.mdc` | Auto-fetch library docs via Context7 |
| `dgx-spark-platform.mdc` | Hardware, Dashboard, operational constraints |
| `dgx-spark-ngc.mdc` | Use `nvcr.io` not Docker Hub |
| `china-mirrors-no-proxy.mdc` | Domestic mirrors; no proxy by default |
| `build-nvidia-spark.mdc` | build.nvidia.com/spark playbooks hub (primary resource) |
| `command-format.mdc` | One command per line |

### Skills (`.cursor/skills/`)

| Skill | Purpose |
|-------|---------|
| `context7/` | Context7 MCP documentation workflow |
| `dgx-spark/` | Machine ops, Dashboard, Docker, troubleshooting |
| `china-mirrors/` | Domestic mirrors (pip, npm, HF, apt, Docker, GitHub) |
| `build-spark/` | build.nvidia.com/spark playbooks catalog & workflows |
| `nvidia-ai-stack/` | NGC, models, vLLM, TensorRT-LLM, NIM |
| `learning-buddy/` | Product architecture, milestones, repo boundaries |
| `agents-dev/` | Cursor SDK, agents, MCP integration |

### MCP (`.cursor/mcp.json`)

| Server | Mode | Requires |
|--------|------|----------|
| `context7` | Local stdio | Optional `CONTEXT7_API_KEY` |
| `sequential-thinking` | Local stdio | Node.js (installed) |
| `memory` | Local stdio | Node.js (installed) |
| `github` | Remote (optional) | PAT + see `mcp.optional.json` |

**Known fix**: proxy `127.0.0.1:7897` breaks remote MCP — see `.cursor/MCP.md`.

**Refresh**: Cursor Settings → MCP → Refresh after config changes.

### Tools (`tools/`)

```bash
tools/check-dgx-health.sh
```

```bash
tools/setup-system.sh
```

```bash
tools/setup-china-mirrors.sh
```

```bash
tools/setup-china-mirrors-system.sh
```

## Quick Start

1. Dashboard: http://localhost:11000
2. Playbooks: https://build.nvidia.com/spark (see `build-spark` skill)
3. Health check: `tools/check-dgx-health.sh`
4. Pull PyTorch: `docker pull nvcr.io/nvidia/pytorch:25.01-py3`

## Workspace Layout

**Cursor 工作区根目录**：`~/workspace/`（2026-06 起；`~/doc`、`~/tools`、`~/AGENTS.md` 为指向此处的符号链接，兼容旧路径）。

```
~/workspace/                 # Cursor 工作区 & git 根
  AGENTS.md                  # 本文件
  doc/                       # 产品文档、架构图、plans
  tools/                     # DGX 运维脚本（镜像、健康检查、MCP）
  models/                    # → ../opt/models（30GB 权重，不纳入 git）
  exam-ocr-server/           # C2 网关（:8000）
  learning-buddy-c2/         # Docker 编排（vLLM + gateway）
  vllm-tune/                 # vLLM 调参
  agents/                    # agent 项目（LangGraph R&D）
  notebooks/                 # Jupyter 实验
  scripts/                   # 通用自动化
  configs/                   # 推理/serving 配置
```

**用户级（不在 git 内）**：`~/.cursor/`（rules、skills、MCP）、`~/opt/models/`（模型实体）、`~/.bashrc.d/`（环境变量）。

## API Keys

Copy `tools/env.example` → add to `~/.bashrc`, then restart Cursor.
