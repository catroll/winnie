# 28 · langchain-retriever

**检索器**：Question → Retriever → Top K Documents。

```text
Question → Retriever → Top K Documents →（后续）LLM
```

## 学习目标

1. 用 `as_retriever` 把 VectorStore 暴露成统一检索接口。
2. 理解 similarity search / Top-K（以及 MMR 等变体的定位）。
3. 用 `-q` 换问题，观察命中文档如何变化。

```bash
cd playground/28langchain-retriever && uv sync && uv run python main.py
uv run python main.py -q "Winnie 用什么做版本管理？"
```
