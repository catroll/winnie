# 43 · langchain-tracing

**调用链追踪**：Trace ID / Span / Latency / Token。

```text
Request → Prompt → LLM → Tool → Retriever → LLM
```

## 学习目标

1. 为一次调用打上 `trace_id` / tags / metadata。
2. 对照 `logs/` 中的 ask/answer 理解 Span 边界。
3. 联想对接 OpenTelemetry / LangSmith 等追踪系统。

```bash
cd playground/43langchain-tracing && uv sync && uv run python main.py
```
