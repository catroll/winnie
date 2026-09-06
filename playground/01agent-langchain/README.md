# 01agent-langchain

基于 **LangChain** 的最小 Agent 示例，用来熟悉基本概念（非 Winnie 主产品代码）。

## 概念对照

| 概念 | 本示例中的位置 |
|------|----------------|
| **Model** | `init_chat_model` — 可换 OpenAI 兼容端点 |
| **Tools** | `agent/tools.py` — `@tool` 声明可调用能力 |
| **System Prompt** | `agent/__init__.py` — 角色与行为约束 |
| **Agent Loop** | `create_agent` — Model 发起 tool_calls ↔ 执行 Tool ↔ 再推理，直到直接回答 |
| **Messages / State** | `invoke({"messages": ...})` — 对话状态在消息列表中累积 |

运行后加默认会打印「消息轨迹」，便于看到工具调用与最终回答如何串起来。

## 运行

```bash
cd playground/01agent-langchain
cp .env.example .env   # 填写 OPENAI_API_KEY / BASE_URL / MODEL
uv sync
uv run python main.py
uv run python main.py "用计算器算 2** 不支持；改算 2+2，并读取偏好 answer_lang"
```

## 目录

```text
01agent-langchain/
├── agent/
│   ├── __init__.py   # build_agent
│   └── tools.py      # Tools
├── main.py           # CLI + 轨迹打印
├── pyproject.toml
└── .env.example
```
