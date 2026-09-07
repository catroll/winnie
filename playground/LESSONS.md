# Playground 调试经验（19–50）

补充 Agent / RAG / Middleware / 生产化章节时踩坑与约定。

## 依赖

1. **`langchain_core.embeddings.FakeEmbeddings` 与 `InMemoryVectorStore` 都依赖 numpy**  
   教学环境尽量少装依赖：用 `_shared/rag_data.py` 的 `HashEmbeddings` + `SimpleVectorStore`（纯 Python 余弦相似度）。
2. **`HashEmbeddings` 必须带「词桶」**  
   纯 SHA 向量几乎无语义重叠，RAG 演示会答非所问。当前实现：一半维度按 token 哈希累加，一半噪声，保证含相同关键词的 chunk 更容易命中。
3. **切分默认 `chunk_size=80, overlap=16`**  
   过小（40）易把「Markdown / Git / Qdrant」拆散，检索命中差、模型只能胡猜。

## Agent

1. **Human-in-the-loop（23）** 默认 `input()`；冒烟用 `--yes` / `--no`，避免阻塞 CI/脚本。
2. **Memory（21）** 靠 `InMemorySaver` + 固定 `thread_id`；新 thread 不会自动带上旧历史。
3. **Fallback（48）** 用假模型名触发主链失败，再 `with_fallbacks`；日志里会先有一条 `answer(error)`。

## RAG 链路

```text
Document → Split → HashEmbed → SimpleVectorStore → Retriever → Prompt → LLM
```

共用语料在 `_shared/rag_data.py`（产品说明 / 开发约定 / 演示里程碑），与 Winnie 设计文档对齐，便于后续真接 Qdrant。

## 观测与成本

1. **Callback 必须传 `BaseCallbackHandler` 实例**  
   第 44 章曾把 `Metrics` 数据类直接塞进 `callbacks=`，触发 `ignore_chain` AttributeError；应传 `MetricsHandler(metrics)`。
2. DeepSeek 等兼容接口的 `token_usage` 字段可能不全或偏大，成本估算仅作示意。
