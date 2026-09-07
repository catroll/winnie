# 41 · langchain-memory-store

**Memory Store**：短期会话记忆 vs 长期画像存储。

```text
Memory
├── Short Term（Session / Checkpoint）
└── Long Term（Store / Redis / DB）
```

## 学习目标

1. 区分 short-term（thread checkpointer）与 long-term store。
2. 演示跨会话注入用户偏好等长期记忆。
3. 联想生产中 Redis / Database 作为 Memory Store 的落点。

```bash
cd playground/41langchain-memory-store && uv sync && uv run python main.py
```
