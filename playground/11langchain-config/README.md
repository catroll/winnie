# 11 · langchain-config

教学 Demo 第 11 章：**运行配置**。

```text
同一条 Chain
     ↓
RunnableConfig
     ├── tags
     ├── metadata
     └── configurable.model_name
     ↓
dev / test / prod  →  不同模型（动态切换）
```

## 学习目标

学完本章应能：

1. 在 `invoke` / `stream` 时传入 **`RunnableConfig`**，而不是改链的构造代码。
2. 使用 **tags**、**metadata** 做追踪与分层（环境、功能、预览信息）。
3. 用 **`configurable_fields` / `ConfigurableField`** 在运行时切换 `model_name`。
4. 实现 **dev / test / prod** 三套模型映射，一次部署多环境行为可配置。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **RunnableConfig** | 每次调用的运行时字典：`tags` / `metadata` / `configurable` / `callbacks` 等。 |
| **tags** | 短标签列表，便于过滤日志与追踪（如 `env:prod`）。 |
| **metadata** | 结构化附加信息（环境名、功能名、业务字段），给回调与可观测性用。 |
| **configurable fields** | 把模型（或温度等）声明为可配置；调用时 `configurable={"model_name": "..."}` 覆盖。 |
| **环境切换** | Chain 定义一次；dev/test/prod 只换 Config，不 fork 三份代码。 |

## 建议重点理解

- **配置属于「这一次调用」**，不是全局单例乱改；并发下各请求可带不同 Config。
- tags/metadata **不改变模型推理内容**（除非你自己读它们写进 Prompt）；它们服务运维与治理。
- 与第 10 章 Factory：Factory 解决「怎么构造 Provider」；Config 解决「同链上这次用哪个可调参数」。

## 运行

```bash
cd playground/11langchain-config
cp .env.example .env
uv sync
uv run python main.py
uv run python main.py --env prod -q "当前是生产配置吗？"
```

在输出中留意：

- `[config] configurable=...`
- `[config] tags=...` / `metadata=...`（Callback 探针）

## 目录

```text
11langchain-config/
├── config_demo/    # configurable model + config_for_env
├── main.py
├── logs/
├── pyproject.toml
└── .env.example
```
