# 25 · langchain-splitter

**文本切分**：Chunk Size / Chunk Overlap。

```text
Document → Chunk1 / Chunk2 / Chunk3 / …
```

## 学习目标

1. 对比 Character / Recursive 等切分思路（本示例走共享 `split_docs`）。
2. 体会 `chunk_size` 与 `chunk_overlap` 对块数与边界的影响。
3. 理解切分质量会直接拉高或拉垮后续 RAG 效果。

```bash
cd playground/25langchain-splitter && uv sync && uv run python main.py
uv run python main.py --size 40 --overlap 10
uv run python main.py --size 80 --overlap 20
```
