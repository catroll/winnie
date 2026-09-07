# 01 · langchain-demo

教学 Demo 第 1 章：**程序启动与大模型基础交互**。

```text
用户输入
   ↓
LangChain（组装 Message / Prompt，调用 ChatModel）
   ↓
LLM
   ↓
输出结果
```

## 学习目标

学完本章应能：

1. 用 LangChain 初始化一个 **ChatModel**（含 OpenAI 兼容端点）。
2. 区分 **SystemMessage** 与 **HumanMessage**，并写出基础 Prompt。
3. 调用 **`invoke()`** 完成一次请求→响应。
4. 在 `logs/` 中对照 ask/answer，看清「发给模型的内容」和「模型返回」。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **ChatModel** | 对话模型抽象；业务代码依赖接口，不绑死某一家 HTTP SDK。本示例用 `init_chat_model("openai:...")`。 |
| **`invoke()`** | 同步一次调用：输入 Messages（或等价结构）→ 返回模型输出。流式对应 `stream` / `astream`，本章先掌握 invoke。 |
| **Message 角色** | **System**：人设与硬规则；**Human**：用户本轮输入。角色决定模型如何解释上下文。 |
| **基础 Prompt** | System 里写清角色、约束、输出风格；Human 承载任务。Prompt 质量直接影响工具选择与回答稳定性。 |
| **调用参数 ≠ 仅文本** | `tools` 等走 API 的 `invocation_params`，不一定出现在 System 正文里。看 ask 日志时要连同 `invocation_params` 一起读。 |

### 本 Demo 额外涉及（可作预习）

当前代码在「基础交互」之上，还演示了 **Tools**（`@tool`）与 **Agent Loop**（`create_agent`：模型可发起 `tool_calls` → 执行工具 → 再推理）。可先当黑盒跑通，细节放到后续「Tool / Agent」章深入。

| 扩展概念 | 代码位置 |
|----------|----------|
| Tools | `agent/tools.py` |
| System Prompt / 组装 Agent | `agent/__init__.py` |
| 轨迹打印与入口 | `main.py` |

## 建议重点理解

- LangChain 是**编排层**：把 Message、模型、（可选）工具收成一次可调用流水线。
- 一次 `invoke` = 至少一轮程序↔LLM；Agent 场景下可能多轮 LLM 调用（多对 ask/answer 日志）。
- 先会「无状态单轮」：发什么、回什么；再谈记忆与 Agent。

## 运行

```bash
cd playground/01langchain-demo
cp .env.example .env   # 填写 OPENAI_API_KEY / BASE_URL / MODEL
uv sync
uv run python main.py
uv run python main.py "用计算器算 2+2，并把偏好 answer_lang 记为 zh"
```

LLM 落盘约定见 `.cursor/rules/playground-llm-logs.mdc`。

## 目录

```text
01langchain-demo/
├── agent/
│   ├── __init__.py   # ChatModel + Prompt + Agent 组装
│   └── tools.py      # Tools（本章扩展）
├── main.py           # 入口 / 轨迹 / 落盘
├── logs/             # ask · answer
├── pyproject.toml
└── .env.example
```
