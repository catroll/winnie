# 26 · langchain-embedding

**Embedding 向量化**：文本 → 向量（教学用 `HashEmbeddings`）。

```text
Text → Embedding Model → Vector
```

## 学习目标

1. 理解 `embed_documents` / `embed_query` 的输入输出形态。
2. 使用 **`HashEmbeddings`**（确定性假向量，无 numpy）观察文本→向量映射。
3. 为 VectorStore / Retriever 章节准备「可复现、无云端 Embedding」的基础。

```bash
cd playground/26langchain-embedding && uv sync && uv run python main.py
```
