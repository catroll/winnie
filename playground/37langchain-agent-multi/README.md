# 37 · langchain-agent-multi

**Multi Agent**：Supervisor 调度多个子 Agent（工具形态演示）。

```text
Supervisor
 ├── Research Agent
 ├── Coding Agent
 ├── Data Agent
 └── Writing Agent
```

## 学习目标

1. 用 Supervisor + 子能力工具模拟多 Agent 协作。
2. 理解「先研究再写作」这类编排顺序。
3. 为后续真正的 LangGraph 多 Agent 拓扑做铺垫。

```bash
cd playground/37langchain-agent-multi && uv sync && uv run python main.py
uv run python main.py -q "研究 Winnie 记忆架构并写一段介绍"
```
