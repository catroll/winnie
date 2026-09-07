# 30 · langchain-rag-chat

**带上下文的 RAG（Conversational RAG）**。

```text
User → Chat History →（可选 Rewrite）→ Retriever → LLM
```

## 学习目标

1. 在多轮对话中同时携带 Chat History 与检索 Context。
2. 理解指代型追问（「它的价格呢？」）为何依赖历史。
3. 对比无历史的单轮 RAG，体会 Conversational RAG 的差异。

```bash
cd playground/30langchain-rag-chat && uv sync && uv run python main.py
```
