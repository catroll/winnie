# 31 · langchain-rag-query

**Query Rewrite**：把含糊追问改写成可检索问句。

```text
「它多少钱？」→ Rewrite →「公司产品价格是多少？」→ Retriever
```

## 学习目标

1. 用 LLM 做 Query Rewrite / History-aware 检索前置。
2. 对比改写前后的检索命中差异。
3. 为 Conversational RAG 补上「问题独立化」这一环。

```bash
cd playground/31langchain-rag-query && uv sync && uv run python main.py
```
