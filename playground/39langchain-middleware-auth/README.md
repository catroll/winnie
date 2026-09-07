# 39 · langchain-middleware-auth

**权限控制**：按角色暴露不同 Tool。

```text
User → Auth Middleware → Agent（Admin / User / Guest 工具集不同）
```

## 学习目标

1. 用 `--role` 切换 admin / user / guest 可用工具集。
2. 理解「鉴权发生在 Agent 之前」比事后拦截更安全。
3. 观察 guest 无工具时直接拒绝执行的行为。

```bash
cd playground/39langchain-middleware-auth && uv sync && uv run python main.py
uv run python main.py --role user
uv run python main.py --role admin
uv run python main.py --role guest
```
