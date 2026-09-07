# 42 · langchain-checkpoint

**状态持久化**：步骤间 Checkpoint，崩溃后可 Resume。

```text
Agent Step 1 → Checkpoint → Agent Step 2 →（Crash）→ Resume
```

## 学习目标

1. 用同一 `thread_id` + checkpointer 续跑多步对话。
2. 理解 Checkpoint 与 LangGraph 持久化高度相关。
3. 区分内存 Saver 与可落盘后端对「真·崩溃恢复」的差异。

```bash
cd playground/42langchain-checkpoint && uv sync && uv run python main.py
```
