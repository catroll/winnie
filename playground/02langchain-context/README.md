# 02 · langchain-context

教学 Demo 第 2 章：**多轮会话与上下文**。

```text
第 1 轮: [System] + Human₁ → AI₁
第 2 轮: [System] + Human₁ + AI₁ + Human₂ → AI₂
  …
总结轮:  …完整历史… +「请总结」→ 结构化总结
```

## 学习目标

学完本章应能：

1. 说明 **Chat History / Message History** 由哪些 Message 组成，以及每轮如何增长。
2. 正确使用 **SystemMessage / HumanMessage / AIMessage**，并在代码里**自行累积**历史再 `invoke`。
3. 解释：LangChain **不会自动记忆**；所谓 Memory 本质是**管理并重新注入** Message History。
4. 在多轮结束后发起一次「总结」调用，把对话沉淀为结构化输出（本 Demo 写入 `logs/summary-*.md`）。

## 核心知识点

| 知识点              | 要理解什么                                                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------- |
| **Chat History**    | 对话到当前为止的完整记录；下一轮模型「记得什么」，完全取决于你塞进本次请求的历史。                      |
| **Message History** | 用有序列表保存 Message；常见顺序：`System` → `(Human → AI)*` → 本轮 `Human`。                           |
| **SystemMessage**   | 通常整段会话共用一条（或少数几条）；改人设等于改后续所有轮的前提。                                      |
| **HumanMessage**    | 用户每一轮输入；多轮即多条 Human，中间夹着对应的 AI。                                                   |
| **AIMessage**       | 模型上一轮输出；若不追加回列表，下一轮模型看不到自己说过的话。                                          |
| **上下文传递**      | 每次 `invoke(messages)` 传入**当前完整列表**（或经裁剪/摘要后的等价上下文），而不是只发最新一句。       |
| **上下文窗口**      | 历史越长 token 越多、越贵、越易超窗；生产上要有截断、摘要、检索式记忆等策略（本章先掌握「显式注入」）。 |
| **收尾总结**        | 多轮信息可再开一轮专用 Prompt，把 History 压成结构化知识——贴近 Winnie「交互必沉淀」的思路。             |

## 建议重点理解

> **LangChain 本身不会自动记忆。**  
> 框架不会在两次 `invoke` 之间偷偷替你保存对话。  
> 行业里说的 Memory，本质上是：**你（或 Memory 组件）把历史 Message 存起来，并在下一次调用时重新注入到 messages 里。**

对照本 Demo：`main.py` 里的 `messages` 列表就是最朴素的 Memory——追加 Human/AI，再整表传入。

常见误区：

- 以为「用了 LangChain 就有记忆」→ 没有自动记忆，只有你注入的上下文。
- 只把最新 Human 发给模型 → 模型无法引用更早轮次。
- 忘记把 AIMessage 写回 History → 下一轮上下文残缺，行为会飘。

## LangChain 上下文管理

最朴素的方式是「全量上下文」：每轮对话都把完整的 `messages` 传给模型。

```text
messages.append(HumanMessage(...))
        ↓
model.invoke(messages)
        ↓
追加本轮 AIMessage
        ↓
下一轮继续发送完整 messages
```

此时 `messages` 包含开头的 System，以及此前所有 Human / AI 消息。没有截断、摘要、滑动窗口，也没有使用 Memory 或 Checkpointer。所谓“记忆”，本质上就是自己维护这份 `messages`，每次调用时重新发送给 LLM。

这种方式简单直观，但随着对话增长，Context 和 Token 消耗会不断增加。

更工程化的做法是进行**上下文管理（Context Management）**，核心是控制“哪些信息应该进入当前 Context”。

常见方式包括：

1. 滑动窗口：只保留最近 N 轮对话，控制 Context 长度。
2. 历史摘要：将较早的对话压缩成 Summary，再结合最近对话发送给模型。

   ```text
   早期对话 → Summary
                 +
             最近 N 轮
                 ↓
               LLM
   ```

3. 长期记忆：从对话中提取用户偏好、重要事实等长期有效信息，单独存储，需要时注入 Context。
4. 检索历史：将历史对话存入外部存储，根据当前问题检索相关内容，而不是全部发送。

因此可以简单理解为：

```text
基础：全部历史 → Context

进阶：最近历史 + Summary

高级：最近历史
    + 重要事实
    + 按需检索的历史
    + 当前任务 Context
```

**Context ≠ Memory**：Context 是“本次发送给 LLM 的信息”；Memory 是“如何保存、筛选和管理历史信息”。

没有单独一章把这四块拼成「高级 Context」；是拆在后面几章里的：

| README 里的能力           | 更接近的章节                                                                           |
| ------------------------- | -------------------------------------------------------------------------------------- |
| 最近历史（窗口 / 会话内） | **21** agent-memory、**42** checkpoint（按 thread 续聊）                               |
| 重要事实（长期偏好等）    | **41** memory-store（短记 vs 长记注入）                                                |
| 按需检索的历史            | **28–31** retriever / RAG / rag-chat / query-rewrite（历史当事料库检索，而不是整包塞） |
| 当前任务 Context          | **29** RAG 的 `{context}`、**03** prompt；Agent 侧见 **35** rag-agent                  |

若要对齐高级公式，最短路径：**21 → 41 → 30（或 31）**；再加 **42** 看持久化。

## 与第 1 章的关系

|         | 01langchain-demo                  | 02langchain-context          |
| ------- | --------------------------------- | ---------------------------- |
| 焦点    | 单次（或 Agent 内多步）调用怎么通 | **跨用户轮次**的上下文怎么保 |
| History | 单轮为主                          | 显式累积多轮 Message         |
| 工具    | 有 Tools / Agent（扩展）          | 无工具，专心 History         |

## 运行

```bash
cd playground/02langchain-context
cp .env.example .env   # 或复用 ../01langchain-demo/.env
uv sync
uv run python main.py
uv run python main.py --turn "我叫 Cat" --turn "时区 UTC+8" --turn "偏好先给结论"
```

查看 `logs/*-ask.txt`：越到后面的轮次，`## messages` 越长——这就是上下文在变长。

## 目录

```text
02langchain-context/
├── conversation/   # System Prompt、Demo turns、build_model
├── main.py         # 累积 History → 多轮 invoke → 总结
├── logs/           # ask/answer + summary-*.md
├── pyproject.toml
└── .env.example
```
