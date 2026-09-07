# LangChain Demo 学习主题清单

建议你不要按 LangChain 的 API 文档机械学习，而是按**能力递进 + 每个主题一个独立 Demo**来做。这样最终可以从简单 Chat 程序，逐步演进到完整 Agent。

## 第一阶段：基础模型能力

### 01. langchain-demo

**程序启动，大模型基础交互**

知识点：

- ChatModel
- invoke()
- System / Human Message
- 基础 Prompt

Demo：

```text
用户输入
   ↓
LangChain
   ↓
LLM
   ↓
输出结果
```

---

### 02. langchain-context

**多轮会话上下文**

知识点：

- Chat History
- Message History
- SystemMessage
- HumanMessage
- AIMessage
- 上下文传递

建议重点理解：

> LangChain 本身不会自动记忆，所谓 Memory 本质上是管理和重新注入 Message History。

---

### 03. langchain-prompt

**PromptTemplate 与提示词工程**

知识点：

- PromptTemplate
- ChatPromptTemplate
- MessagesPlaceholder
- Partial Variables
- Few-shot Prompt

Demo：

```text
用户问题
   ↓
Prompt Template
   ↓
变量填充
   ↓
LLM
```

建议做几个 Demo：

```text
普通 Prompt
变量 Prompt
Few-shot Prompt
动态 Prompt
```

---

### 04. langchain-output

**结构化输出**

知识点：

- Structured Output
- Pydantic
- JSON Schema
- Output Parser

Demo：

用户：

```text
分析这段新闻
```

模型输出：

```json
{
  "title": "",
  "summary": "",
  "keywords": [],
  "sentiment": ""
}
```

这是非常重要的能力。

因为企业 AI 应用通常不是：

```text
LLM → Text
```

而是：

```text
LLM → Structured Data → Program
```

---

# 第二阶段：LCEL 与 Runnable

这一阶段是 LangChain 的核心。

---

### 05. langchain-runnable

**Runnable 基础**

知识点：

- Runnable
- RunnableLambda
- RunnableSequence
- RunnableParallel

理解：

```text
LangChain Pipeline
```

例如：

```text
Input
 ↓
Prompt
 ↓
Model
 ↓
Parser
```

---

### 06. langchain-lcel

**LCEL 管道编排**

重点：

```python
prompt | model | parser
```

知识点：

- Pipe
- Chain
- Sequence
- Composition

Demo：

```text
用户问题
   ↓
Prompt
   ↓
LLM
   ↓
Parser
```

---

### 07. langchain-parallel

**并行执行**

知识点：

- RunnableParallel
- 并行任务
- 多模型调用

Demo：

```text
                 ┌─ 标题生成
用户文章 ────────┼─ 摘要生成
                 ├─ 情感分析
                 └─ 关键词提取
```

最后：

```json
{
  "title": "",
  "summary": "",
  "keywords": [],
  "sentiment": ""
}
```

这个 Demo 很适合学习企业级 AI Pipeline。

---

### 08. langchain-stream

**流式输出**

知识点：

- stream()
- astream()
- Token Streaming
- Callback

Demo：

```text
LLM

Token1
Token2
Token3
Token4
```

最终可以接：

```text
CLI
Web
SSE
WebSocket
```

---

### 09. langchain-async

**异步调用**

知识点：

- ainvoke()
- asyncio
- astream()
- 并发控制

Demo：

```text
Task1 ─┐
Task2 ─┼── asyncio.gather()
Task3 ─┘
```

建议你重点测试：

```text
串行
并行
限流
超时
重试
```

---

# 第三阶段：模型与 Middleware

### 10. langchain-model

**统一模型接口**

知识点：

- ChatOpenAI
- 不同 LLM Provider
- Model Abstraction

Demo：

```text
OpenAI
DeepSeek
Qwen
Claude

      ↓

LangChain Interface
```

实现一个：

```python
ModelFactory
```

---

### 11. langchain-config

**运行配置**

知识点：

- RunnableConfig
- tags
- metadata
- configurable fields

Demo：

```text
不同模型

dev
test
prod
```

动态切换。

---

### 12. langchain-callback

**Callback 回调机制**

知识点：

- Callback Handler
- LLM Start
- LLM End
- Tool Start
- Error

Demo：

实现：

```text
AI 调用日志
```

输出：

```text
Prompt
Token
Latency
Model
Cost
Error
```

这个非常适合你的工程背景。

---

### 13. langchain-retry

**异常处理与重试**

知识点：

- Retry
- Timeout
- Fallback
- Error Handling

Demo：

```text
LLM A
 ↓失败

Retry
 ↓

LLM A
 ↓失败

Fallback
 ↓

LLM B
```

---

# 第四阶段：Tool

这是 Agent 的基础。

---

### 14. langchain-tool

**自定义 Tool**

知识点：

- @tool
- Tool Schema
- Tool Description

Demo：

```python
@tool
def get_weather(city: str):
```

重点理解：

```text
Python Function

↓

Tool Schema

↓

LLM Function Calling
```

---

### 15. langchain-tools

**多个 Tool**

Demo：

```text
Agent

 ├── Weather Tool
 ├── Search Tool
 ├── Calculator Tool
 └── Database Tool
```

学习：

- Tool Selection
- Tool Calling
- Tool Result

---

### 16. langchain-tool-schema

**Tool 参数定义**

知识点：

- Pydantic
- JSON Schema
- 参数校验

Demo：

```python
class SearchInput(BaseModel):
    keyword: str
    limit: int
```

理解：

```text
LLM

↓

JSON Arguments

↓

Pydantic Validation

↓

Python Function
```

---

### 17. langchain-tool-error

**Tool 错误处理**

Demo：

```text
Tool Error

↓

Error Message

↓

LLM Retry

↓

New Arguments
```

这个很关键。

真实 Agent 最大的问题之一就是：

```text
Tool 调用失败
```

---

# 第五阶段：Agent

这是 LangChain 最核心的应用方向。

---

### 18. langchain-agent

**第一个 Agent**

Demo：

```text
User

↓

Agent

↓

LLM

↓

Answer
```

学习：

- create_agent
- Agent Loop
- State

---

### 19. langchain-agent-tool

**Agent + Tool**

Demo：

用户：

```text
北京今天天气怎么样？
```

Agent：

```text
Thought

↓

Weather Tool

↓

Observation

↓

Answer
```

重点理解：

> Agent = LLM + Tool + Loop

---

### 20. langchain-agent-multi-tool

**多工具 Agent**

Demo：

```text
用户：
查北京天气，并计算未来三天平均温度

Agent

↓

Weather Tool

↓

Calculator Tool

↓

Answer
```

---

### 21. langchain-agent-memory

**Agent + Memory**

Demo：

```text
User:
我叫 Woody

AI:
你好 Woody

User:
我叫什么？

AI:
Woody
```

架构：

```text
Agent

├── Chat History
├── User Context
└── Session Context
```

---

### 22. langchain-agent-loop

**理解 Agent Loop**

建议不要只调用 API。

自己打印：

```text
================

STEP 1

LLM

↓

Tool Call

================

STEP 2

Tool Result

↓

LLM

================
```

彻底理解：

```text
Reasoning Loop
```

---

### 23. langchain-agent-human

**Human in the Loop**

Demo：

```text
Agent:

我要删除数据库

↓

Human Approval

↓

Yes

↓

Execute
```

适合理解：

- Approval
- Interrupt
- Resume

---

# 第六阶段：RAG

这是企业 AI 最常见的场景。

---

### 24. langchain-loader

**Document Loader**

知识点：

- TextLoader
- PDF Loader
- Web Loader
- CSV Loader

Demo：

```text
PDF

↓

Document
```

---

### 25. langchain-splitter

**文本切分**

知识点：

- CharacterTextSplitter
- RecursiveCharacterTextSplitter
- Chunk Size
- Chunk Overlap

Demo：

```text
Document

↓

Chunk1
Chunk2
Chunk3
Chunk4
```

重点理解：

```text
Chunk Size
Chunk Overlap
```

对 RAG 效果影响很大。

---

### 26. langchain-embedding

**Embedding 向量化**

Demo：

```text
Text

↓

Embedding Model

↓

Vector
```

例如：

```text
"苹果手机"

↓

[0.123, -0.42, ...]
```

---

### 27. langchain-vectorstore

**向量数据库**

建议依次测试：

```text
InMemory

Chroma

FAISS

pgvector
```

Demo：

```text
Document

↓

Embedding

↓

Vector Store
```

---

### 28. langchain-retriever

**检索器**

知识点：

- similarity search
- MMR
- Retriever

Demo：

```text
Question

↓

Retriever

↓

Top K Documents

↓

LLM
```

---

### 29. langchain-rag

**完整 RAG**

这是重要 Demo。

架构：

```text
Document

↓

Loader

↓

Splitter

↓

Embedding

↓

Vector Store


Question

↓

Retriever

↓

Context

↓

Prompt

↓

LLM

↓

Answer
```

---

### 30. langchain-rag-chat

**带上下文的 RAG**

Demo：

```text
User:
介绍公司产品

AI:
XXXX

User:
它的价格呢？

↓

Chat History

↓

Question Rewrite

↓

Retriever

↓

LLM
```

这是：

```text
Conversational RAG
```

---

### 31. langchain-rag-query

**Query Rewrite**

Demo：

```text
User:

它多少钱？

↓

Rewrite

↓

公司产品价格是多少？

↓

Retriever
```

学习：

- Query Rewrite
- History-aware Retriever

---

### 32. langchain-rag-multi-query

**多 Query 检索**

Demo：

```text
Question

↓

LLM

↓

Query1
Query2
Query3

↓

Retriever

↓

Merge
```

---

### 33. langchain-rag-rerank

**Rerank**

架构：

```text
Query

↓

Vector Search

↓

Top 20

↓

Rerank

↓

Top 5

↓

LLM
```

这个是企业 RAG 的重要能力。

---

### 34. langchain-rag-hybrid

**混合检索**

Demo：

```text
Query

        ┌── Vector Search
        │
────────┤
        │
        └── BM25

↓

Rerank

↓

LLM
```

---

# 第七阶段：Agent + RAG

### 35. langchain-rag-agent

**RAG Tool**

把 Retriever 封装成：

```text
Knowledge Tool
```

Agent：

```text
Agent

├── Search Tool
├── Database Tool
└── Knowledge Tool
```

---

### 36. langchain-agent-router

**路由 Agent**

Demo：

```text
Question

↓

Router

├── 普通聊天
├── RAG
├── Database
└── API
```

例如：

```text
天气？

→ Weather Tool

公司制度？

→ RAG

订单多少？

→ Database
```

---

### 37. langchain-agent-multi

**Multi Agent**

架构：

```text
Supervisor

 ├── Research Agent
 ├── Coding Agent
 ├── Data Agent
 └── Writing Agent
```

这个阶段建议开始关注：

```text
LangGraph
```

---

# 第八阶段：Middleware

### 38. langchain-middleware

**Agent Middleware**

建议学习：

```text
Before Model

↓

LLM

↓

After Model
```

用途：

- Prompt 注入
- 日志
- 权限
- Token 控制
- 安全控制

---

### 39. langchain-middleware-auth

**权限控制**

Demo：

```text
User

↓

Auth Middleware

↓

Agent
```

不同用户：

```text
Admin

User

Guest
```

拥有不同 Tool。

---

### 40. langchain-middleware-limit

**限流与 Token 控制**

Demo：

```text
Request

↓

Rate Limit

↓

Token Budget

↓

LLM
```

企业系统必须考虑。

---

# 第九阶段：Persistence

### 41. langchain-memory-store

**Memory Store**

学习：

```text
Memory

├── Short Term
│
└── Long Term
```

Demo：

```text
Session Memory

↓

Redis / Database
```

---

### 42. langchain-checkpoint

**状态持久化**

Demo：

```text
Agent Step 1

↓

Checkpoint

↓

Agent Step 2

↓

Crash

↓

Resume
```

这个知识点开始与：

```text
LangGraph
```

高度关联。

---

# 第十阶段：Observability

### 43. langchain-tracing

**调用链追踪**

重点：

```text
Request

↓

Prompt

↓

LLM

↓

Tool

↓

Retriever

↓

LLM
```

记录：

```text
Trace ID
Span
Latency
Token
Cost
Error
```

---

### 44. langchain-observability

**AI 可观测性**

Demo：

```text
Metrics

├── QPS
├── Latency
├── Token
├── Cost
├── Tool Error
└── RAG Hit Rate
```

这个你可以结合：

```text
Prometheus
Grafana
Loki
```

做一个企业级 Demo。

---

### 45. langchain-evaluation

**AI 评估**

知识点：

- Dataset
- Test Case
- LLM Judge
- RAG Evaluation

Demo：

```text
Question

Expected Answer

Actual Answer

↓

Evaluation
```

---

# 第十一阶段：生产级能力

### 46. langchain-cache

**LLM Cache**

架构：

```text
Request

↓

Cache

├── Hit → Return
│
└── Miss

      ↓

     LLM
```

可以测试：

```text
Memory Cache
Redis Cache
Semantic Cache
```

---

### 47. langchain-batch

**批量调用**

Demo：

```text
Input1
Input2
Input3
Input4

↓

Batch

↓

LLM
```

学习：

- batch()
- abatch()
- 并发

---

### 48. langchain-fallback

**模型降级**

架构：

```text
GPT

↓

Error

↓

Claude

↓

Error

↓

DeepSeek
```

---

### 49. langchain-cost

**Token 成本统计**

Demo：

```text
Request

↓

LLM

↓

Input Token

Output Token

↓

Cost
```

---

### 50. langchain-production

**完整生产 Demo**

建议最后做一个：

# AI Assistant Platform

架构：

```text
                  API

                   ↓

                Gateway

                   ↓

              LangChain

                   ↓

                Agent

        ┌──────────┼──────────┐

        ↓          ↓          ↓

       RAG        Tool      Memory

        ↓          ↓          ↓

     Vector DB    API      Redis

        ↓

      Database


Observability

Prometheus
Grafana
Tracing
Logging
```

---

# 推荐你的最终项目目录

建议保持统一编号：

```text
langchain-demo/
langchain-context/
langchain-prompt/
langchain-output/

langchain-runnable/
langchain-lcel/
langchain-parallel/
langchain-stream/
langchain-async/

langchain-model/
langchain-config/
langchain-callback/
langchain-retry/

langchain-tool/
langchain-tools/
langchain-tool-schema/
langchain-tool-error/

langchain-agent/
langchain-agent-tool/
langchain-agent-multi-tool/
langchain-agent-memory/
langchain-agent-loop/
langchain-agent-human/

langchain-loader/
langchain-splitter/
langchain-embedding/
langchain-vectorstore/
langchain-retriever/

langchain-rag/
langchain-rag-chat/
langchain-rag-query/
langchain-rag-multi-query/
langchain-rag-rerank/
langchain-rag-hybrid/

langchain-rag-agent/
langchain-agent-router/
langchain-agent-multi/

langchain-middleware/
langchain-middleware-auth/
langchain-middleware-limit/

langchain-memory-store/
langchain-checkpoint/

langchain-tracing/
langchain-observability/
langchain-evaluation/

langchain-cache/
langchain-batch/
langchain-fallback/
langchain-cost/

langchain-production/
```

---

# 我建议的学习优先级

如果你的目标是**尽快掌握 LangChain，并为 LangGraph 打基础**，建议按下面顺序。

说明：左侧 `01–28` 是**精简学习路径自己的序号**（约 28 个主题）；右侧 `→ NN` 是上文**完整 50 章清单**里的章号。这是**子集映射，不是 50 章一一重排**——未出现在右侧的完整章号，表示本路径**暂未列入**，也**没有并入**左边某一主题（仍可按完整清单单独学，或自行并进邻近 Demo）。

```text
第一阶段
├── 01 demo            → 01
├── 02 context         → 02
├── 03 prompt          → 03
├── 04 output          → 04
├── 05 runnable        → 05
├── 06 LCEL            → 06
└── 07 stream          → 08

第二阶段
├── 08 tool            → 14
├── 09 tool schema     → 16
├── 10 agent           → 18
├── 11 agent tool      → 19
└── 12 agent loop      → 22

第三阶段
├── 13 loader          → 24
├── 14 splitter        → 25
├── 15 embedding       → 26
├── 16 vectorstore     → 27
├── 17 retriever       → 28
└── 18 RAG             → 29

第四阶段
├── 19 RAG Chat        → 30
├── 20 Query Rewrite   → 31
├── 21 Rerank          → 33
├── 22 Hybrid Search   → 34
└── 23 RAG Agent       → 35

第五阶段
├── 24 Middleware      → 38
├── 25 Memory          → 41
├── 26 Observability   → 44
├── 27 Evaluation      → 45
└── 28 Production      → 50

第六阶段
└── LangGraph          （完整清单无独立章；多 Agent 处建议切入，见 37）
```

本路径**未列入**的完整章号（举例）：

| 完整章       | 主题                              | 说明                                       |
| ------------ | --------------------------------- | ------------------------------------------ |
| 07           | parallel                          | 未并入；与 LCEL 相关，需要时可跟 06 一起练 |
| 09           | async                             | 未并入；可跟 stream / batch 补             |
| 10–13        | model / config / callback / retry | 基础设施，可并进 common                    |
| 15 / 17      | tools / tool-error                | 可并进 tool / agent-tool                   |
| 20 / 21 / 23 | multi-tool / agent-memory / human | Agent 加深，按需补                         |
| 32           | multi-query                       | 可并进 Query Rewrite / RAG                 |
| 36           | router                            | 可并进 RAG Agent                           |
| 39–40        | middleware-auth / limit           | 可并进 Middleware                          |
| 42–43        | checkpoint / tracing              | 可并进 Memory / Observability              |
| 46–49        | cache / batch / fallback / cost   | 可并进 Production                          |

Playground 目录仍按**完整 01–50**建章，与上表精简路径无关。

## 我的明确建议

**不要真的做 50 个彼此独立、非常小的 Demo。** 对你这种有工程经验的开发者，更高效的方式是控制在 **30 个左右**，每个 Demo 有一个明确的技术主题，同时不断复用前面的基础设施。

尤其建议你后续建立一个统一基础包：

```text
langchain-demos/

├── common/
│   ├── config.py
│   ├── model.py
│   ├── logging.py
│   └── callback.py
│
├── 01-langchain-demo/
├── 02-langchain-context/
├── 03-langchain-prompt/
...
```

这样学到后面，你实际上是在逐步构建一个 **AI Application Framework 的雏形**，而不是单纯刷 API。

**下一步我建议直接从 `03langchain-prompt` 开始，并且我可以继续按每个主题给你设计：学习目标 → 核心 API → Demo 功能 → 项目结构 → 验收标准。**
