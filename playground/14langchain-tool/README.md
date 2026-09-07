# 14 · langchain-tool

**自定义 Tool**：`@tool` / Schema / Description。

```text
Python Function → Tool Schema → LLM Function Calling
```

## 学习目标

1. 用 `@tool` 把函数变成可供模型调用的工具。
2. 读懂 name / description / 参数 schema（模型靠描述选工具）。
3. 在 Agent 中完成一次「问天气 → 调 get_weather → 回答」。

```bash
cd playground/14langchain-tool && uv sync && uv run python main.py
uv run python main.py --schema-only
```
