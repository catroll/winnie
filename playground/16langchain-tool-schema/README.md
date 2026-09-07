# 16 · langchain-tool-schema

**Tool 参数定义**：Pydantic / JSON Schema / 校验。

```text
LLM → JSON Arguments → Pydantic Validation → Python Function
```

## 学习目标

1. 用 `args_schema=BaseModel` 约束工具入参。
2. 导出 JSON Schema，理解模型看到的参数说明。
3. 体会非法参数在校验层失败，而不是静默进业务函数。

```bash
cd playground/16langchain-tool-schema && uv sync && uv run python main.py
```
