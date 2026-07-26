# Corpus 运营契约：Structure（PDF 导入）→ Wiki / Graph / Vector

## 硬边界

| 职责 | 归属 |
|------|------|
| **分章节 / TOC 加深 / 一叶一块** | **仅 PDF 教材导入流水线**（`corpus_import_incremental` + `textbook_toc_deepen`） |
| Wiki 展示、Graph 挂边、publish、reindex、RAG | **通用知识库** — **不分章**，只消费已定稿 `section`/`chunk` |

禁止在 `corpus_publish`、reindex、`app/rag/*` 中调用分章。

```text
【PDF 教材导入】
  IR → 原 TOC 初树 →（过粗）全书 AI 按知识点加深 → 叶=toc_leaf chunk
       ↓
【通用 KB】
  Wiki / Graph / publish → Vector（dense + lexical RRF → rerank → graph_expand）
```

## 其它来源（不进 PDF 分章模块）

| 来源 | 边界在哪定 |
|------|------------|
| 试卷 | 各自导入流水线：一题一单元 |
| 培训 | 生成侧已有章节 |

## PDF 教材分章原则

1. 先保原 TOC / heading 骨架。
2. 过粗叶节：AI 按 **一个知识点一个小节** 加深（如 `1.1` → `1.1.1`…）；**禁止字数硬拆**。
3. 字数只作软信号（`TEXTBOOK_TOC_DEEPEN_SOFT_CHARS`）。
4. 失败：`needs_structure_review` → WorkQueue.`structure_review_imports`。

## 三层消费（定稿之后）

| 层 | 人 | 系统 |
|----|----|------|
| Wiki | 审 HTML / TOC / AI 子节 | Studio 按目录渲染 |
| Graph | 确认 primary_kp / 语义边 | 结构边自动；语义边人确认后 publish |
| Vector | staging 试搜 → publish | 仅 published；降级删向量 |

## 检索链（通用 KB，不分章）

```text
Milvus dense → lexical sparse 排序 → RRF → rerank → graph_expand → EvalContext
```

配置：`RAG_HYBRID_RRF_ENABLED`、`RAG_RRF_K`。

## 部署

改 `learning-buddy-api/app/**` 后须重建 **buddy-api 与 buddy-worker**（代码打进镜像）。

## 相关代码

- 导入分章：`app/services/textbook_toc_deepen.py`、`corpus_tree_from_ir.py`、`corpus_import_incremental.py`
- 检索：`app/rag/retriever.py`、`app/rag/rrf.py`、`app/rag/vector/milvus_store.py`
