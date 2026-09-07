# 06 · langchain-lcel

教学 Demo 第 6 章：**LCEL 管道编排**。

```text
用户问题
   ↓
Prompt
   ↓
LLM
   ↓
Parser
```

代码核心形态：

```python
chain = prompt | model | parser
chain.invoke({"question": "..."})
```

## 学习目标

学完本章应能：

1. 用 **`|`（Pipe）** 把 Prompt、Model、Parser 写成一条 **Chain**。
2. 说明 Pipe / Chain / Sequence / Composition 在 LCEL 里如何对应。
3. 对已有 chain 再 `|` 新节点（后处理），体验 **Composition（组合）**。
4. 通过只替换 Prompt、复用 Model/Parser，理解「编排与实现解耦」。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **LCEL** | LangChain Expression Language：用表达式组合 Runnable，而不是手写逐步 `invoke`。 |
| **Pipe `|`** | 组合运算符：`a \| b` 表示 a 的输出作为 b 的输入；左右都必须是 Runnable。 |
| **Chain** | 组合后的可调用管道（仍是 Runnable），一次 `invoke` 跑完整段。 |
| **Sequence** | Pipe 背后的串行语义（与第 5 章 `RunnableSequence` 同一件事，两种写法）。 |
| **Composition** | 小链拼大链：`basic \| postprocess`；或换 Prompt 得到新 Chain。 |
| **Parser** | 本 Demo 用 `StrOutputParser`，把 `AIMessage` 收成 `str`，方便打印与下游拼接。 |

本 Demo `--mode`：

| mode | 演示 |
|------|------|
| `basic` | `prompt \| model \| parser` |
| `compose` | `basic \| RunnableLambda`（再组合） |
| `swap_prompt` | 换 Prompt，Model/Parser 不变 |

## 建议重点理解

- **声明数据流，而不是过程脚本**：先写清 `prompt | model | parser`，再考虑分支与并行。
- **`|` 要求类型在边界对齐**：Prompt 输出 Messages → Model 输出 AIMessage → Parser 输出 str。
- 第 5 章讲积木（Lambda / Sequence / Parallel）；**本章强调书写习惯与组合思维**——企业流水线多从此形态长出来。

与第 5 章关系：`RunnableSequence(a,b,c)` ≈ `a | b | c`；日常优先读、写 `|` 形式。

## 运行

```bash
cd playground/06langchain-lcel
cp .env.example .env   # 或复用 ../01langchain-demo/.env
uv sync
uv run python main.py
uv run python main.py --mode compose -q "LCEL 和直接 invoke 模型有何不同？"
```

## 目录

```text
06langchain-lcel/
├── chains/         # basic / compose / swap_prompt
├── main.py
├── logs/
├── pyproject.toml
└── .env.example
```
