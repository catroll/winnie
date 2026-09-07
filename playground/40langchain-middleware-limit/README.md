# 40 · langchain-middleware-limit

**限流与 Token 控制**：企业系统必备闸门。

```text
Request → Rate Limit → Token Budget → LLM
```

## 学习目标

1. 用并发信号量演示 Rate Limit（`--concurrency`）。
2. 理解 Token Budget 对成本与稳定性的意义。
3. 把限流/预算当作可观测、可配置的横切策略。

```bash
cd playground/40langchain-middleware-limit && uv sync && uv run python main.py
uv run python main.py --concurrency 2
uv run python main.py --concurrency 1
```
