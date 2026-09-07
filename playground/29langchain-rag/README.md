# 29 · langchain-rag

**完整 RAG**：Loader → Split → Embed → Store → Retrieve → Prompt → LLM。

```text
Document → Loader → Splitter → Embedding → Vector Store
Question → Retriever → Context → Prompt → LLM → Answer
```

## 学习目标

1. 把前几章组件串成一条可运行的问答链路。
2. 区分「索引阶段」与「查询阶段」各自的输入输出。
3. 用真实问题验证：答案应扎根于检索到的 Context。

```bash
cd playground/29langchain-rag && uv sync && uv run python main.py
uv run python main.py -q "知识怎么存储？"
```
