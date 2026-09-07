# 03 · langchain-prompt

教学 Demo 第 3 章：**PromptTemplate 与提示词工程**。

```text
用户问题
   ↓
Prompt Template（选模板 / 填变量 / 拼 Few-shot / 插入历史）
   ↓
变量填充 → Messages（或字符串）
   ↓
LLM
```

本 Demo 覆盖四类用法：

| 类型 | 含义 | `--mode` |
|------|------|----------|
| 普通 Prompt | `PromptTemplate` 字符串填充 | `plain` |
| 变量 Prompt | `ChatPromptTemplate` + 多槽位 / `partial` | `variable` |
| Few-shot Prompt | 样例对嵌入后再问 | `fewshot` |
| 动态 Prompt | `MessagesPlaceholder` 注入历史 | `dynamic` |

## 学习目标

学完本章应能：

1. 区分 **PromptTemplate**（字符串）与 **ChatPromptTemplate**（Message 列表）。
2. 用槽位变量与 **`partial`** 预填固定参数。
3. 用 **MessagesPlaceholder** 把运行时历史/动态内容插入模板。
4. 用 **Few-shot** 模板提供输入/输出样例，约束模型风格与粒度。
5. 在 `logs/*-ask.txt` 中核对照：填充后的 messages 是否符合预期。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **PromptTemplate** | 字符串模板 + `{variables}`；适合拼一段纯文本。Chat 模型最终仍通常包成 Message。 |
| **ChatPromptTemplate** | 按角色组装 System/Human/AI 等；`format_messages(...)` 得到 `list[BaseMessage]`。对话场景优先用它。 |
| **变量填充** | `format` / `format_messages` / `invoke` 时传入字典；缺变量会报错。 |
| **Partial Variables** | `template.partial(k=v)` 提前钉死部分槽位（如产品名、角色），调用处只传变化项。 |
| **MessagesPlaceholder** | 模板里的「插槽」，运行时传入 `list[BaseMessage]`（历史、检索片段等）。连接第 2 章 History 与模板的桥梁。 |
| **Few-shot Prompt** | 在正式问题前插入若干 Human/AI 样例，用示例教格式，而不是只靠 System 文字描述。 |
| **提示词工程** | 模板把「稳定结构」与「每次变化的数据」分开；工程上可版本管理、复用、单测填充结果。 |

## 建议重点理解

- **模板 ≠ 模型记忆**：模板只决定「这一次怎么组 Prompt」；跨轮记忆仍要靠第 2 章的 History 注入（常用 `MessagesPlaceholder("history")`）。
- **先看填充结果再调模型**：本 Demo 打印「填充后的 Messages」；调试 Prompt 时优先看这个，而不是只看最终回答。
- **Few-shot 耗 token**：样例越多越稳也越贵；样例要短、要对齐目标格式。

## 运行

```bash
cd playground/03langchain-prompt
cp .env.example .env   # 或复用 ../01langchain-demo/.env
uv sync
uv run python main.py                 # 四类全部跑一遍
uv run python main.py --mode fewshot
uv run python main.py --mode dynamic -q "向量库是什么？"
```

## 目录

```text
03langchain-prompt/
├── prompts/        # 各类 Template 定义
├── main.py         # 按 mode 填充 → invoke → 落盘
├── logs/
├── pyproject.toml
└── .env.example
```
