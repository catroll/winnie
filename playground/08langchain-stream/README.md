# 08 · langchain-stream

教学 Demo 第 8 章：**流式输出**。

```text
LLM
  → Token1
  → Token2
  → Token3
  → Token4
```

最终同一套 chunk 流可以接到：

```text
CLI · Web · SSE · WebSocket
```

差别只是「每个 token/块写到哪里」，不是另学一套模型 API。

## 学习目标

学完本章应能：

1. 用 **`stream()`** 同步迭代输出块，在 CLI 实时打印。
2. 用 **`astream()`** 在 asyncio 中消费同一管道（为 Web/ASGI 做准备）。
3. 理解 **Token Streaming**：模型边生成边推送，降低首字延迟。
4. 用 **Callback**（`on_llm_new_token`）观察/转发 token，而不只依赖 for 循环变量。
5. 说清流式与 `invoke` 的取舍：聊天 UI 优先 stream；批处理/结构化常 `invoke`。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **`stream()`** | 同步生成器：`for chunk in chain.stream(input)`。 |
| **`astream()`** | 异步异步迭代：`async for chunk in chain.astream(input)`。 |
| **Token Streaming** | 底层 chat 模型以 token（或细粒度块）推送；LCEL 中 `StrOutputParser` 会把块收成文本片段。 |
| **Callback** | `on_llm_new_token` / start / end；适合日志、打点、旁路推送（本 Demo 打印 `TokenN`）。 |
| **对接形态** | CLI=`print`；SSE=`data: ...\n\n`；WebSocket=`send_text`；本质都是消费 chunk。 |

本 Demo `--mode`：

| mode | 演示 |
|------|------|
| `stream` | 同步边到边输出 |
| `astream` | asyncio 异步流 |
| `callback` | `TokenPrintHandler` 逐 token 编号打印 |

## 建议重点理解

- **流的是 Runnable 管道**，不只是裸模型：`prompt | model | parser` 同样可以 `.stream()`。
- **首 token 延迟**往往比总耗时更影响聊天体验。
- 结构化输出（第 4 章）很多场景仍用 `invoke`；流式更适合「给人看的正文」。
- `logs/` 里 ask/answer 仍会落盘（完整调用）；屏幕上的 TokenN 是流式过程的可视化。

## 运行

```bash
cd playground/08langchain-stream
cp .env.example .env
uv sync
uv run python main.py
uv run python main.py --mode callback -q "用三句话说明 SSE 适合什么场景"
```

## 目录

```text
08langchain-stream/
├── streamer/       # chain + TokenPrintHandler
├── main.py
├── logs/
├── pyproject.toml
└── .env.example
```
