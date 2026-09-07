# 05 · langchain-runnable

教学 Demo 第 5 章：**Runnable 基础**（LangChain 流水线的积木）。

```text
Input
 ↓
Prompt
 ↓
Model
 ↓
Parser
```

一切可编排单元都实现 **Runnable**：统一具备 `invoke` / `batch` / `stream` 等接口，才能用 `|` 或 `RunnableSequence` 串起来。

## 学习目标

学完本章应能：

1. 说明什么是 **Runnable**，以及为何 Prompt / Model / Parser 都能进同一条管道。
2. 用 **RunnableLambda** 把普通函数变成管道节点（预处理、后处理）。
3. 用 **RunnableSequence**（或等价的 `a | b | c`）组装 **Input → Prompt → Model → Parser**。
4. 用 **RunnableParallel** 对同一输入扇出多路，得到结构化多字段结果。
5. 分清：**Sequence = 串行依赖**；**Parallel = 同输入并行分支**（更深并行见后续章节）。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **Runnable** | LangChain 的可执行协议。ChatModel、PromptTemplate、OutputParser 等都是 Runnable。 |
| **`invoke(input)`** | 同步跑完整条（或单个）Runnable；输入输出类型由管道约定（常是 `str` / `dict` / `Message`）。 |
| **RunnableLambda** | 包装 Python 函数：`RunnableLambda(fn)`，用于清洗输入、改 shape、拼结果。 |
| **RunnableSequence** | 串行：上一步输出 = 下一步输入。`RunnableSequence(a, b, c)` ≈ `a \| b \| c`。 |
| **RunnableParallel** | 并行：同一输入同时进多个分支，输出为 `dict[分支名, 结果]`。 |
| **RunnablePassthrough** | 把输入原样带到下游（常嵌在 Parallel 里）；本 Demo 用 Lambda 取出 `question` 字段，效果类似「保留原问题」。 |
| **Pipeline** | 业务上的「提示词 → 模型 → 解析」链路；实现上就是 Runnable 的组合。 |

本 Demo `--mode`：

| mode | 演示 |
|------|------|
| `sequence` | 显式 `RunnableSequence(Prompt, Model, Parser)` |
| `pipe` | 同上，用 `prompt \| model \| parser` 书写 |
| `lambda` | `RunnableLambda` 预处理 `str→dict` 再进管道 |
| `parallel` | `RunnableParallel`：标题 + 回答两路并行 |

## 建议重点理解

- **组合优于堆砌 API**：先想数据怎么流过节点，再选 Lambda / Sequence / Parallel。
- **类型要在节点边界对齐**：Prompt 常吃 `{"question": ...}`，Lambda 负责把 `str` 转成 dict。
- **`|` 不是魔法**：它是 Runnable 的组合语法；本质仍是 Sequence（第 6 章 LCEL 会再强调）。
- Parallel 的每个分支仍是一条小 Pipeline；总结果是字典，便于接程序（呼应第 4 章结构化思维）。

## 运行

```bash
cd playground/05langchain-runnable
cp .env.example .env   # 或复用 ../01langchain-demo/.env
uv sync
uv run python main.py
uv run python main.py --mode parallel -q "Qdrant 适合个人知识库吗？"
```

## 目录

```text
05langchain-runnable/
├── pipeline/       # Lambda / Sequence / Parallel 组装
├── main.py
├── logs/
├── pyproject.toml
└── .env.example
```
