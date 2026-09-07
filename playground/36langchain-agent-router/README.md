# 36 · langchain-agent-router

**路由 Agent**：按意图分发到不同工具/能力。

```text
Question → Router → 普通聊天 / RAG / Database / API
```

## 学习目标

1. 用多工具 Agent 模拟「天气 / 知识 / 订单」路由。
2. 观察同一 Agent 对不同问题选择不同 Tool。
3. 可用多次 `-q` 自定义一组路由用例。

```bash
cd playground/36langchain-agent-router && uv sync && uv run python main.py
uv run python main.py -q "北京天气？" -q "Winnie 知识怎么存？" -q "订单 1001 状态？"
```
