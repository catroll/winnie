# Playground

LangChain **教学 Demo 组**（01–50）。总览见 [langchain-topic.md](./langchain-topic.md)。

| 章节 | 目录 | 说明 |
|------|------|------|
| — | [_shared](./_shared) | llm 落盘 / model / rag_data |
| 01 | [01langchain-demo](./01langchain-demo) | 见该目录 README |
| 02 | [02langchain-context](./02langchain-context) | 见该目录 README |
| 03 | [03langchain-prompt](./03langchain-prompt) | 见该目录 README |
| 04 | [04langchain-output](./04langchain-output) | 见该目录 README |
| 05 | [05langchain-runnable](./05langchain-runnable) | 见该目录 README |
| 06 | [06langchain-lcel](./06langchain-lcel) | 见该目录 README |
| 07 | [07langchain-parallel](./07langchain-parallel) | 见该目录 README |
| 08 | [08langchain-stream](./08langchain-stream) | 见该目录 README |
| 09 | [09langchain-async](./09langchain-async) | 见该目录 README |
| 10 | [10langchain-model](./10langchain-model) | 见该目录 README |
| 11 | [11langchain-config](./11langchain-config) | 见该目录 README |
| 12 | [12langchain-callback](./12langchain-callback) | 见该目录 README |
| 13 | [13langchain-retry](./13langchain-retry) | 见该目录 README |
| 14 | [14langchain-tool](./14langchain-tool) | 见该目录 README |
| 15 | [15langchain-tools](./15langchain-tools) | 见该目录 README |
| 16 | [16langchain-tool-schema](./16langchain-tool-schema) | 见该目录 README |
| 17 | [17langchain-tool-error](./17langchain-tool-error) | 见该目录 README |
| 18 | [18langchain-agent](./18langchain-agent) | 见该目录 README |
| 19 | [19langchain-agent-tool](./19langchain-agent-tool) | 见该目录 README |
| 20 | [20langchain-agent-multi-tool](./20langchain-agent-multi-tool) | 见该目录 README |
| 21 | [21langchain-agent-memory](./21langchain-agent-memory) | 见该目录 README |
| 22 | [22langchain-agent-loop](./22langchain-agent-loop) | 见该目录 README |
| 23 | [23langchain-agent-human](./23langchain-agent-human) | 见该目录 README |
| 24 | [24langchain-loader](./24langchain-loader) | 见该目录 README |
| 25 | [25langchain-splitter](./25langchain-splitter) | 见该目录 README |
| 26 | [26langchain-embedding](./26langchain-embedding) | 见该目录 README |
| 27 | [27langchain-vectorstore](./27langchain-vectorstore) | 见该目录 README |
| 28 | [28langchain-retriever](./28langchain-retriever) | 见该目录 README |
| 29 | [29langchain-rag](./29langchain-rag) | 见该目录 README |
| 30 | [30langchain-rag-chat](./30langchain-rag-chat) | 见该目录 README |
| 31 | [31langchain-rag-query](./31langchain-rag-query) | 见该目录 README |
| 32 | [32langchain-rag-multi-query](./32langchain-rag-multi-query) | 见该目录 README |
| 33 | [33langchain-rag-rerank](./33langchain-rag-rerank) | 见该目录 README |
| 34 | [34langchain-rag-hybrid](./34langchain-rag-hybrid) | 见该目录 README |
| 35 | [35langchain-rag-agent](./35langchain-rag-agent) | 见该目录 README |
| 36 | [36langchain-agent-router](./36langchain-agent-router) | 见该目录 README |
| 37 | [37langchain-agent-multi](./37langchain-agent-multi) | 见该目录 README |
| 38 | [38langchain-middleware](./38langchain-middleware) | 见该目录 README |
| 39 | [39langchain-middleware-auth](./39langchain-middleware-auth) | 见该目录 README |
| 40 | [40langchain-middleware-limit](./40langchain-middleware-limit) | 见该目录 README |
| 41 | [41langchain-memory-store](./41langchain-memory-store) | 见该目录 README |
| 42 | [42langchain-checkpoint](./42langchain-checkpoint) | 见该目录 README |
| 43 | [43langchain-tracing](./43langchain-tracing) | 见该目录 README |
| 44 | [44langchain-observability](./44langchain-observability) | 见该目录 README |
| 45 | [45langchain-evaluation](./45langchain-evaluation) | 见该目录 README |
| 46 | [46langchain-cache](./46langchain-cache) | 见该目录 README |
| 47 | [47langchain-batch](./47langchain-batch) | 见该目录 README |
| 48 | [48langchain-fallback](./48langchain-fallback) | 见该目录 README |
| 49 | [49langchain-cost](./49langchain-cost) | 见该目录 README |
| 50 | [50langchain-production](./50langchain-production) | 见该目录 README |

各章：`cd <dir> && uv sync && uv run python main.py`（可复用 `01langchain-demo/.env`）。

LLM 落盘约定：`.cursor/rules/playground-llm-logs.mdc`。

调试经验：见 [LESSONS.md](./LESSONS.md)。
