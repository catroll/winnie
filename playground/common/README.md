# playground/common

各章共用的可编辑包（`winnie-playground-common`）。

- `common.llm_record` — LLM ask/answer 落盘
- `common.model` — `build_chat_model` / `load_env`
- `common.rag_data` — 教学语料、切分、`HashEmbeddings`、`SimpleVectorStore`

各章在 `pyproject.toml` 中声明：

```toml
dependencies = [
    "winnie-playground-common",
    ...
]

[tool.uv.sources]
winnie-playground-common = { path = "../common", editable = true }
```

导入：`from common.llm_record import LlmInteractionRecorder`（无需 `sys.path`）。
