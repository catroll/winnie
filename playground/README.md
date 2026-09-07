# Playground

LangChain **教学 Demo 组**：按章节学习核心知识点（非 Winnie 主产品代码）。主题总览见 [langchain-topic.md](./langchain-topic.md)。

| 章节 | 目录 | 主题 |
|------|------|------|
| — | [_shared](./_shared) | 共用：LLM 落盘 / `build_chat_model` |
| 01 | [01langchain-demo](./01langchain-demo) | 启动与大模型基础交互 |
| 02 | [02langchain-context](./02langchain-context) | 多轮上下文 |
| 03 | [03langchain-prompt](./03langchain-prompt) | Prompt 模板 |
| 04 | [04langchain-output](./04langchain-output) | 结构化输出 |
| 05 | [05langchain-runnable](./05langchain-runnable) | Runnable 基础 |
| 06 | [06langchain-lcel](./06langchain-lcel) | LCEL 管道编排 |
| 07 | [07langchain-parallel](./07langchain-parallel) | 并行执行 |
| 08 | [08langchain-stream](./08langchain-stream) | 流式输出 |
| 09 | [09langchain-async](./09langchain-async) | 异步调用 |
| 10 | [10langchain-model](./10langchain-model) | 统一模型接口 |
| 11 | [11langchain-config](./11langchain-config) | 运行配置 |
| 12 | [12langchain-callback](./12langchain-callback) | Callback 调用日志 |
| 13 | [13langchain-retry](./13langchain-retry) | 重试 / 超时 / Fallback |
| 14 | [14langchain-tool](./14langchain-tool) | 自定义 Tool |
| 15 | [15langchain-tools](./15langchain-tools) | 多 Tool 选择与调用 |
| 16 | [16langchain-tool-schema](./16langchain-tool-schema) | Tool 参数 Schema |
| 17 | [17langchain-tool-error](./17langchain-tool-error) | Tool 错误与恢复 |
| 18 | [18langchain-agent](./18langchain-agent) | 第一个 Agent |

各章细节见对应目录 `README.md`。

## LLM 交互落盘（各章统一）

约定：`.cursor/rules/playground-llm-logs.mdc`。

- 路径：各示例 `logs/`
- 文件：`yyyymmdd-hhmmss-nnnnnnnnn-ask.txt` / `...-answer.txt`
- 实现：`_shared/llm_record.py`
