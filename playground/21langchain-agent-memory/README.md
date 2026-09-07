# 21 · langchain-agent-memory

**Agent + Memory**：会话记忆与用户上下文。

```text
Agent
├── Chat History
├── User Context
└── Session Context
```

## 学习目标

1. 用 checkpointer / `thread_id` 保持多轮对话状态。
2. 验证「我叫 Woody」后追问「我叫什么？」能召回姓名。
3. 区分 Chat History 与更广义的 User/Session Context。

```bash
cd playground/21langchain-agent-memory && uv sync && uv run python main.py
```
