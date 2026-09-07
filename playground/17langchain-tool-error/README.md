# 17 · langchain-tool-error

**Tool 错误处理**（真实 Agent 高频问题）。

```text
Tool Error → Error Message → LLM Retry → New Arguments
```

## 学习目标

1. 让工具在非法入参时抛出**可读错误**（而不是崩溃进程）。
2. 观察 Agent 如何根据 ToolMessage 中的错误改参数再调。
3. 理解：工具失败恢复靠「错误信息质量」+「模型循环」；优先把错误作为 Tool 返回值，而不是未处理的异常打断 Agent。

```bash
cd playground/17langchain-tool-error && uv sync && uv run python main.py
```
