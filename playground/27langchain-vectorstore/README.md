# 27 · langchain-vectorstore

**向量数据库**：Document → Embedding → Vector Store。

```text
Document → Embedding → Vector Store → similarity_search
```

## 学习目标

1. 把切分后的文档写入（教学）向量库并完成相似度检索。
2. 理解 InMemory 等后端在本地 demo 中的角色（生产可换成 Chroma / FAISS / pgvector）。
3. 观察 `metadata` + `page_content` 如何作为检索结果返回。

```bash
cd playground/27langchain-vectorstore && uv sync && uv run python main.py
```
