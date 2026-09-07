# 44 · langchain-observability

**AI 可观测性**：QPS / Latency / Token / Cost / Error 等指标。

```text
Metrics
├── QPS
├── Latency
├── Token
├── Cost
├── Tool Error
└── RAG Hit Rate
```

## 学习目标

1. 用 Callback 聚合 calls / errors / tokens / latency。
2. 理解「可观测」是生产 AI 的一等公民，而非事后补丁。
3. 联想 Prometheus / Grafana / Loki 的企业级落地。

```bash
cd playground/44langchain-observability && uv sync && uv run python main.py
```
