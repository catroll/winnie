# 04 · langchain-output

教学 Demo 第 4 章：**结构化输出**（企业 AI 的关键能力）。

```text
用户：分析这段新闻
        ↓
      LLM
        ↓
{
  "title": "",
  "summary": "",
  "keywords": [],
  "sentiment": ""
}
        ↓
   下游 Program（校验 / 入库 / 触发流程）
```

## 为什么重要

企业应用通常不是：

```text
LLM → Text
```

而是：

```text
LLM → Structured Data → Program
```

自由文本难自动消费；**有 Schema 的结构**才能稳定接业务代码、API、数据库与工作流。

## 学习目标

学完本章应能：

1. 用 **Pydantic** 定义输出合约（字段、类型、约束、说明）。
2. 用 **`with_structured_output`** 让 ChatModel 直接返回结构化对象（推荐路径）。
3. 理解同一合约的 **JSON Schema** 视图，以及为何工具链常要 schema。
4. 了解经典 **Output Parser**：模型先出 JSON 文本，再解析校验（对比现代路径的差异）。
5. 在代码里把结果当 **数据** 用（`model_dump()` / 字段访问），而不是只 `print` 一段话。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **Structured Output** | 约束模型按固定形状回答；目标是可机器解析的数据，不是散文。 |
| **Pydantic** | Python 侧的结构合约：类型检查、默认值、`Field(description=...)` 可进入提示/工具说明。 |
| **JSON Schema** | 与语言无关的结构描述；`model_json_schema()` 可从 Pydantic 导出；部分 API 直接吃 schema。 |
| **Output Parser** | 把模型**文本**解析成对象（如 `PydanticOutputParser`）。兼容性好，但多一步「生成文本 → 解析」，格式漂移时要重试。 |
| **`with_structured_output`** | 绑定 schema 后 `invoke` 得到 Pydantic/dict。实现上可能是 json_mode / json_schema / function_calling，视模型与网关而定。 |

本 Demo 三条路径（`--mode`）：

| mode | 路径 |
|------|------|
| `pydantic` | `with_structured_output(NewsAnalysis, method="json_mode")` → `NewsAnalysis` |
| `json_schema` | `with_structured_output(schema_dict, method="json_mode")` → `dict` |
| `parser` | Prompt 附带 format_instructions → 文本 → `PydanticOutputParser` |

> 兼容说明：部分 OpenAI 兼容网关不支持 `json_schema` response_format 或 thinking 模式下的 `tool_choice`。本 Demo 默认用 **json_mode**，并在 System 中写明字段与 **json** 字样，以便在更多模型上跑通；概念上仍对应 Structured Output。

## 建议重点理解

- **先定合约，再调模型**：字段名与类型是产品接口，不是「提示词里顺便说说」。
- **校验发生在边界**：Pydantic/Parser 失败要当错误处理（重试、降级、人工），不要假设模型永远老实。
- **结构化 ≠ 取消 Prompt**：仍需说清任务；Schema 管形状，Prompt 管任务与口径。
- 对照 `logs/`：看模型实际返回是否已是结构化通道（tool/json），还是纯文本再解析。

## 运行

```bash
cd playground/04langchain-output
cp .env.example .env   # 或复用 ../01langchain-demo/.env
uv sync
uv run python main.py
uv run python main.py --mode pydantic
uv run python main.py --news "本地团队完成知识库 MVP 演示，情绪高涨。"
```

期望输出形状：

```json
{
  "title": "",
  "summary": "",
  "keywords": [],
  "sentiment": "positive|neutral|negative"
}
```

## 目录

```text
04langchain-output/
├── schema/         # NewsAnalysis（Pydantic）与 JSON Schema
├── main.py         # 三种结构化路径
├── logs/
├── pyproject.toml
└── .env.example
```
