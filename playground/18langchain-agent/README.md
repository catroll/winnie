# 18 · langchain-agent

**第一个 Agent**：`create_agent` / Agent Loop / State。

```text
User → Agent → LLM → Answer
```

## 学习目标

1. 用 `create_agent` 创建最小智能体（可无工具）。
2. 理解 **State** 主要是 `messages` 列表的演进。
3. 在带工具模式下观察 **Agent Loop**（模型 ↔ 工具直至给出最终回答）。
4. 为后续 Agent+Tool / Multi-tool 章节打基础。

`--mode`: `plain`（无工具）| `loop`（含小工具）| `all`

```bash
cd playground/18langchain-agent && uv sync && uv run python main.py
```
