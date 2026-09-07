# 09 · langchain-async

教学 Demo 第 9 章：**异步调用**。

> 说明：你消息里贴的「stream / Token」内容已在 **[08langchain-stream](../08langchain-stream)**。  
> 本章按主题清单实现 **09 · 异步并发**。

```text
Task1 ─┐
Task2 ─┼── asyncio.gather()
Task3 ─┘
```

重点对比：

```text
串行 · 并行 · 限流 · 超时 · （重试示意）
```

## 学习目标

学完本章应能：

1. 用 **`ainvoke()`** 在协程里调用 LCEL 链。
2. 用 **`asyncio.gather`** 并发多个 LLM 任务，并与串行对比耗时。
3. 用 **Semaphore** 做简单 **限流**；用 **`wait_for`** 做 **超时**。
4. 复习 **`astream()`**，分清「异步 API」与「流式输出」是两个维度（可组合）。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **`ainvoke()`** | Runnable 的异步一次性调用；在 `async def` / ASGI 里用它，避免阻塞事件循环。 |
| **`asyncio`** | 调度并发协程；`gather` 同时等待多个任务。 |
| **`astream()`** | 异步迭代输出块（第 8 章）；本章只作衔接复习。 |
| **并发控制** | 无限 gather 可能打爆限流；用信号量限制飞行中请求数。 |
| **超时** | `asyncio.wait_for`；超时抛 `TimeoutError`，由上层决定重试或失败。 |
| **重试** | 本 Demo 在库中提供极简 `ainvoke_with_retry`；生产更常用中间件/策略（后续章节）。 |

本 Demo `--mode`：

| mode | 演示 |
|------|------|
| `compare`（默认） | 串行 vs gather 耗时 |
| `serial` | 逐个 `ainvoke` |
| `gather` | `asyncio.gather` |
| `limit` | Semaphore(2) |
| `timeout` | `wait_for` |
| `astream` | 异步流式 |

## 建议重点理解

- **async ≠ 自动更快**：单任务 ainvoke 与 invoke 延迟相近；快在多任务重叠等待。
- **Parallel（第 7 章）vs gather**：前者是 LCEL 图内扇出；后者是 asyncio 层并发多条 `ainvoke`。场景不同，可同时存在。
- Web 服务（FastAPI）里优先 `ainvoke` / `astream`，把线程留给真正阻塞的工作。

## 运行

```bash
cd playground/09langchain-async
cp .env.example .env
uv sync
uv run python main.py              # 默认 compare
uv run python main.py --mode gather
uv run python main.py --mode limit -q "问1" -q "问2" -q "问3"
```

## 目录

```text
09langchain-async/
├── async_demo/     # serial / gather / limit / timeout
├── main.py
├── logs/
├── pyproject.toml
└── .env.example
```
