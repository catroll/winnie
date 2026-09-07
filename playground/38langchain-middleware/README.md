# 38 · langchain-middleware

**Agent Middleware**：Before Model / After Model 横切能力。

```text
Before Model → LLM → After Model
```

## 学习目标

1. 在模型前后插入 Runnable，做提示增强与输出后处理。
2. 联想 Middleware 常见用途：日志、权限、Token、安全。
3. 为 Auth / Limit 两章的专用中间件打基础。

```bash
cd playground/38langchain-middleware && uv sync && uv run python main.py
```
