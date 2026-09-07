# 12 · langchain-callback

教学 Demo 第 12 章：**Callback 回调机制** → 工程向 **AI 调用日志**。

```text
LLM / Tool 生命周期
        ↓
Callback Handler
        ↓
Prompt · Token · Latency · Model · Cost · Error
```

## 学习目标

学完本章应能：

1. 实现自定义 **Callback Handler**，挂到 `RunnableConfig.callbacks`。
2. 在 **LLM Start / LLM End / Error**（及 **Tool Start**）钩子中采集运行数据。
3. 输出一份可读的调用日志：Prompt、Token、Latency、Model、Cost（估算）、Error。
4. 理解 Callback 适合旁路观测（日志/打点），不代替业务返回值。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **Callback Handler** | 继承 `BaseCallbackHandler`，覆盖生命周期方法。 |
| **LLM Start** | `on_chat_model_start`：拿到 messages / model / run_id，适合记 Prompt。 |
| **LLM End** | `on_llm_end`：拿到 `LLMResult` 与 usage，算 Latency / Token / Cost。 |
| **Tool Start** | `on_tool_start`：Agent 调工具时的名称与入参。 |
| **Error** | `on_llm_error` / `on_tool_error`：失败也要落盘，便于排障。 |

本 Demo `--mode`：

| mode | 演示 |
|------|------|
| `ok` | 成功调用 → 完整字段日志 |
| `tool` | Agent + Tool → `tool_start` |
| `error` | 无效模型 → `Error` 字段 |

日志 JSON 写入 `logs/ai-calls/`；ask/answer 原文仍按约定进 `logs/`。

## 建议重点理解

- **Cost 多为估算**：用环境变量单价 × token；账单以云厂商为准。
- **与第 8/11 章**：流式用 `on_llm_new_token`；Config 的 tags/metadata 可一并进日志（可扩展）。
- 生产可把 `_commit` 改成写 Redis / OpenTelemetry / 自建审计表。

## 运行

```bash
cd playground/12langchain-callback
cp .env.example .env
uv sync
uv run python main.py
uv run python main.py --mode ok -q "Callback 适合记什么？"
```

## 目录

```text
12langchain-callback/
├── logging_cb/     # AiCallLogHandler
├── main.py
├── logs/
│   └── ai-calls/   # 结构化调用日志 JSON
├── pyproject.toml
└── .env.example
```
