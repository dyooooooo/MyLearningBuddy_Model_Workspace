# 双线隔离与仓库边界（摘自工作区 full-isolation / learning-buddy 规则）

## MinerU ↔ 35B 双线隔离基线（勿破坏）

| 层 | 35B | MinerU |
|----|-----|-----|
| Redis 队列 | `import35b` | 默认导入队列 |
| MinIO 前缀 | `import35b/` | `corpus/imports/` |
| Gateway 代理 | `/proxy35b/*` | `/proxy/*` |
| 前端 client | `api/import35b.ts` | `api/corpus.ts` |
| 前端面板 | `B35PipelinePanel` | `MinerUPipelinePanel` |

- 禁止一条线的代码读写另一条线的资源；禁止跨线 import。
- 新增资源前先找现有等价物复用；确需新增必须归位到既有分层。

## 仓库边界

- exam-ocr-server 只做 C2 网关，不放 Buddy 业务逻辑与 A⁺。
- 诊断/规划/多 Agent（①–④）只在 learning-buddy-api。
- 不动学生伴学主链（③ LangGraph / C2 推理）。
