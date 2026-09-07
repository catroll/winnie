# Playground

LangChain **教学 Demo 组**：按章节学习核心知识点（非 Winnie 主产品代码）。

| 章节 | 目录 | 主题 |
|------|------|------|
| — | [_shared](./_shared) | 共用：LLM ask/answer 落盘 |
| 01 | [01langchain-demo](./01langchain-demo) | 启动与大模型基础交互（ChatModel / invoke / Message / Prompt） |
| 02 | [02langchain-context](./02langchain-context) | 多轮上下文（Chat History；Memory = 管理并注入 Message） |
| 03 | [03langchain-prompt](./03langchain-prompt) | PromptTemplate / ChatPromptTemplate / Few-shot / 动态 Prompt |
| 04 | [04langchain-output](./04langchain-output) | 结构化输出（Pydantic / JSON Schema / Output Parser） |
| 05 | [05langchain-runnable](./05langchain-runnable) | Runnable 基础（Lambda / Sequence / Parallel / Pipeline） |
| 06 | [06langchain-lcel](./06langchain-lcel) | LCEL 管道编排（`prompt \| model \| parser`） |
| 07 | [07langchain-parallel](./07langchain-parallel) | 并行执行（RunnableParallel 一文四析 → JSON） |
| 08 | [08langchain-stream](./08langchain-stream) | 流式输出（stream / astream / Token / Callback） |
| 09 | [09langchain-async](./09langchain-async) | 异步调用（ainvoke / gather / 限流 / 超时） |
| 10 | [10langchain-model](./10langchain-model) | 统一模型接口（ModelFactory / 多 Provider） |
| 11 | [11langchain-config](./11langchain-config) | 运行配置（RunnableConfig / tags / 环境切模型） |
| 12 | [12langchain-callback](./12langchain-callback) | Callback 回调（AI 调用日志：Token/Latency/Cost…） |

各章 **学习目标与知识点** 见对应目录 `README.md`。

## LLM 交互落盘（各章统一）

约定：`.cursor/rules/playground-llm-logs.mdc`。

- 路径：各示例 `logs/`
- 文件：`yyyymmdd-hhmmss-nnnnnnnnn-ask.txt` / `...-answer.txt`
- 实现：`_shared/llm_record.py`
