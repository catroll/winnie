# 47 · langchain-batch

**批量调用**：`batch()` / 并发吞吐。

```text
Input1..N → Batch → LLM → Outputs
```

## 学习目标

1. 用 `chain.batch` 一次提交多条输入。
2. 对比串行循环与批量接口的耗时差异直觉。
3. 了解 `abatch` / 并发在生产吞吐中的位置。

```bash
cd playground/47langchain-batch && uv sync && uv run python main.py
```
