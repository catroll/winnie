# 46 · langchain-cache

**LLM Cache**：Hit 直接返回，Miss 再调模型。

```text
Request → Cache → Hit → Return
                 → Miss → LLM → Write Cache
```

## 学习目标

1. 用进程内字典演示 Exact Cache 的 Hit/Miss。
2. 理解重复请求如何省 Token / 降延迟。
3. 联想 Memory / Redis / Semantic Cache 等扩展方向。

```bash
cd playground/46langchain-cache && uv sync && uv run python main.py
```
