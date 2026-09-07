# 48 · langchain-fallback

**模型降级**：主模型失败后切换备用模型。

```text
Primary → Error → Fallback Model → Answer
```

## 学习目标

1. 用 `with_fallbacks` 串起「假失败主链 → 可用副链」。
2. 理解降级对可用性（而非单纯效果）的价值。
3. 思考多供应商链路的错误类型与重试策略衔接。

```bash
cd playground/48langchain-fallback && uv sync && uv run python main.py
```
