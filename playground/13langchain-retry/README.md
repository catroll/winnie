# 13 · langchain-retry

**异常处理与重试**：Retry / Timeout / Fallback。

```text
LLM A → 失败 → Retry → 仍失败 → Fallback → LLM B
```

## 学习目标

1. 用 `with_retry` 对瞬时错误自动重试。
2. 用 `with_fallbacks` 在主模型不可用时切换备用链。
3. 用 `asyncio.wait_for` 做调用超时。
4. 区分：重试（同一路径再试）vs 降级（换路径）。

## 核心知识点

| 概念 | 说明 |
|------|------|
| **Retry** | 同一 Runnable 多次尝试（网络抖、429 等）。 |
| **Timeout** | 超过时限失败，避免挂死。 |
| **Fallback** | 主链失败后走备用模型/链。 |
| **Error Handling** | 失败要可观测；与 Callback 日志配合。 |

`--mode`: `retry` | `fallback` | `timeout` | `all`

```bash
cd playground/13langchain-retry && uv sync && uv run python main.py
```
