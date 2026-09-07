# 23 · langchain-agent-human

**Human in the Loop**：高风险操作前人工审批。

```text
Agent → Human Approval → Yes / No → Execute or Abort
```

## 学习目标

1. 理解 Approval / Interrupt / Resume 三类概念。
2. 在删除数据库等危险工具前插入人工闸门。
3. 用 `--yes` / `--no` 跳过交互，或交互输入审批（实现以 `main.py` 为准）。

```bash
cd playground/23langchain-agent-human && uv sync && uv run python main.py
# 交互审批；或（若已支持）：
# uv run python main.py --yes
# uv run python main.py --no
```
