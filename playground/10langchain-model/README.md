# 10 · langchain-model

教学 Demo 第 10 章：**统一模型接口**。

```text
OpenAI
DeepSeek
Qwen
Claude
      ↓
LangChain Interface（BaseChatModel）
      ↓
业务 Chain（prompt | model | parser）
```

实现：`ModelFactory.create(provider) → ChatModel`。

## 学习目标

学完本章应能：

1. 说明 **Model Abstraction**：业务依赖 `BaseChatModel` / `invoke`，不散落各厂商 SDK。
2. 认识 **ChatOpenAI / `init_chat_model("openai:...")`** 作为 OpenAI 兼容入口的角色。
3. 实现并使用 **ModelFactory**，按 provider 名切换 OpenAI / DeepSeek / Qwen / Claude。
4. 用同一条 LCEL 链换模型，验证「换 Provider 不必改业务编排」。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **ChatOpenAI 路径** | LangChain 里最常见的聊天模型实现；许多云厂商提供 **OpenAI Compatible** API。 |
| **不同 LLM Provider** | 密钥、base_url、model 名不同；协议常可统一到 Chat Completions。 |
| **Model Abstraction** | 上层只拿 `BaseChatModel`；日志、重试、LCEL 都挂在这一层。 |
| **ModelFactory** | 集中解析环境变量 → 构造模型；避免业务里 `if provider == ...` 蔓延。 |

本 Demo：

- `factory/ModelFactory`：注册 openai / deepseek / qwen / claude
- 默认全部走 **OpenAI 兼容** 协议（`init_chat_model("openai:...")`），便于一个网关切换多模型
- Claude 官方 Anthropic API 可另接 `langchain-anthropic`；此处预留兼容 `BASE_URL`，与 Winnie「无厂商锁定」一致

## 建议重点理解

- **兼容层是策略，不是偷懒**：统一接口后，差异收敛到 Factory 配置表。
- **密钥按 Provider 分环境变量**；若只有一把网关 Key，可都回退 `OPENAI_API_KEY`（本 Demo 已支持）。
- 换模型 = 换 Factory 参数；Prompt / Parser / 并行流水线不用重写。

## 运行

```bash
cd playground/10langchain-model
cp .env.example .env   # 或复用 01 的 OPENAI_*；多厂商则分别填写
uv sync
uv run python main.py --list
uv run python main.py --provider openai
uv run python main.py --provider all -q "你是哪家模型？"
```

## 目录

```text
10langchain-model/
├── factory/        # ModelFactory
├── main.py
├── logs/
├── pyproject.toml
└── .env.example
```
