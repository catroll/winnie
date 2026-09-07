# 35 · langchain-rag-agent

**RAG Tool**：把 Retriever 封装成 Knowledge Tool 给 Agent。

```text
Agent
├── Search Tool
├── Database Tool
└── Knowledge Tool  ← Retriever
```

## 学习目标

1. 用 `@tool` 包装向量检索，让 Agent 按需调用知识库。
2. 对比「固定 RAG 链」与「Agent 决定是否检索」两种模式。
3. 用 `-q` 验证知识型问题优先走 `knowledge_search`。

```bash
cd playground/35langchain-rag-agent && uv sync && uv run python main.py
uv run python main.py -q "知识库用什么向量库？"
```
