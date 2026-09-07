# Playground

LangChain **教学 Demo 组**：按章节学习核心知识点（非 Winnie 主产品代码）。

| 章节 | 目录 | 主题 |
|------|------|------|
| — | [_shared](./_shared) | 共用：LLM ask/answer 落盘 |
| 01 | [01langchain-demo](./01langchain-demo) | 启动与大模型基础交互（ChatModel / invoke / Message / Prompt） |
| 02 | [02langchain-context](./02langchain-context) | 多轮上下文（Chat History；Memory = 管理并注入 Message） |
| 03 | [03langchain-prompt](./03langchain-prompt) | PromptTemplate / ChatPromptTemplate / Few-shot / 动态 Prompt |

各章 **学习目标与知识点** 见对应目录 `README.md`。

## LLM 交互落盘（各章统一）

约定：`.cursor/rules/playground-llm-logs.mdc`。

- 路径：各示例 `logs/`
- 文件：`yyyymmdd-hhmmss-nnnnnnnnn-ask.txt` / `...-answer.txt`
- 实现：`_shared/llm_record.py`
