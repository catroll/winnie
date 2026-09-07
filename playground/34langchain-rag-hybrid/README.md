# 34 · langchain-rag-hybrid

**混合检索**：向量检索 + 词法检索（BM25 风格）再合并。

```text
Query ─┬─ Vector Search
       └─ BM25 / Lexical → Merge / Rerank → LLM
```

## 学习目标

1. 并行跑向量相似度与词法命中，并做结果并集。
2. 理解专有名词、短关键词场景下词法检索的价值。
3. 为后续「Hybrid + Rerank」企业流水线打基础。

```bash
cd playground/34langchain-rag-hybrid && uv sync && uv run python main.py
```
