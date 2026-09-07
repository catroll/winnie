# 33 · langchain-rag-rerank

**Rerank**：粗排召回后再精排截断。

```text
Query → Vector Search (Top N) → Rerank → Top K → LLM
```

## 学习目标

1. 区分「向量粗排」与「Rerank 精排」两段检索。
2. 观察 Top-N → Top-K 截断如何影响上下文质量。
3. 了解教学版启发式重排与生产 cross-encoder 的差异。

```bash
cd playground/33langchain-rag-rerank && uv sync && uv run python main.py
```
