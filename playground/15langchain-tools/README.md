# 15 · langchain-tools

**多个 Tool**：选择 / 调用 / 结果回流。

```text
Agent ├── Weather ├── Search ├── Calculator └── Database
```

## 学习目标

1. 一次注册多个 Tool，由模型做 **Tool Selection**。
2. 观察多轮 **Tool Calling** 与 **Tool Result**。
3. 理解工具描述质量直接影响选型对错。

```bash
cd playground/15langchain-tools && uv sync && uv run python main.py
```
