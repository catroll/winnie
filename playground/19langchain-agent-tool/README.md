# 19 · langchain-agent-tool

**Agent + Tool**：LLM + Tool + Loop。

```text
User → Agent → Thought → Weather Tool → Observation → Answer
```

## 学习目标

1. 把自定义工具挂到 Agent，完成「问天气 → 调工具 → 回答」。
2. 理解 Agent = LLM + Tool + Loop，而不是单次 chat。
3. 在 `logs/` 里对照 ask/answer，看清 tool schema 与 tool_calls。

```bash
cd playground/19langchain-agent-tool && uv sync && uv run python main.py
uv run python main.py -q "上海明天会下雨吗？"
```
