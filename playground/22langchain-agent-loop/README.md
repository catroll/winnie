# 22 · langchain-agent-loop

**理解 Agent Loop**：逐步打印 Reasoning Loop。

```text
STEP 1: LLM → Tool Call
STEP 2: Tool Result → LLM
FINAL: Answer
```

## 学习目标

1. 不只看最终答案，而是拆开每一步 LLM / Tool 消息。
2. 识别 `AIMessage.tool_calls` 与 `ToolMessage` 的往返。
3. 建立对 Reasoning Loop 的直觉，便于调试卡住的 Agent。

```bash
cd playground/22langchain-agent-loop && uv sync && uv run python main.py
uv run python main.py -q "请 ping 一下 winnie"
```
