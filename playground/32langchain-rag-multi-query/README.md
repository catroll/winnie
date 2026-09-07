# 32 · langchain-rag-multi-query

**多 Query 检索**：一问扩成多问再合并结果。

```text
Question → LLM → Query1/2/3 → Retriever → Merge
```

## 学习目标

1. 让模型从不同角度扩展检索 query。
2. 对多路结果去重合并，扩大召回面。
3. 理解多 Query 是提升召回的常见企业 RAG 技巧。

```bash
cd playground/32langchain-rag-multi-query && uv sync && uv run python main.py
```
