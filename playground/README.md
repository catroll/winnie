# Playground

实验与示例代码，不进入主产品发布路径。每个子目录为一个独立可运行小项目。

| 目录 | 说明 |
|------|------|
| [_shared](./_shared) | 示例共用工具（含 LLM 交互落盘） |
| [01langchain-demo](./01langchain-demo) | LangChain Agent 基本概念（Tools / Loop） |
| [02langchain-context](./02langchain-context) | 多轮上下文会话 + 收尾结构化总结 |

## LLM 交互落盘（后续示例统一）

约定已写入 Cursor 项目规则：`.cursor/rules/playground-llm-logs.mdc`。

摘要：

- 路径：各示例自己的 `logs/`（如 `01langchain-demo/logs/`）
- 文件：`yyyymmdd-hhmmss-nnnnnnnnn-ask.txt` / `...-answer.txt`
- 实现：`_shared/llm_record.py`
