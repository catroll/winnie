# 49 · langchain-cost

**Token 成本统计**：Input / Output Token → Cost。

```text
Request → LLM → prompt_tokens + completion_tokens → Cost
```

## 学习目标

1. 用 Callback 从 `token_usage` 估算单次与累计费用。
2. 理解定价按百万 Token 计价时的拆分方式。
3. 为限流、缓存、评估等章节提供成本视角。

```bash
cd playground/49langchain-cost && uv sync && uv run python main.py
```
