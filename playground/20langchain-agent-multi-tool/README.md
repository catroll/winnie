# 20 · langchain-agent-multi-tool

**多工具 Agent**：一次任务串起多个 Tool。

```text
User → Agent → Weather Tool → Calculator Tool → Answer
```

## 学习目标

1. 为 Agent 注册多个工具，并观察模型如何选型/串联。
2. 理解多步 Observation 如何汇入最终回答。
3. 用一句话需求触发「查天气 + 算平均」类组合调用。

```bash
cd playground/20langchain-agent-multi-tool && uv sync && uv run python main.py
uv run python main.py -q "查北京天气，并计算未来三天平均温度"
```
