# 02langchain-context

多轮会话示例：**Messages 累积为上下文**，对话结束后再请求一次**结构化总结**。

## 概念对照

| 概念 | 本示例 |
|------|--------|
| **Context** | 同一 `messages` 列表贯穿全程；每轮把 Human + AI 追加进去再 `invoke` |
| **Multi-turn** | 预设多轮用户发言（或 `--turn` 自定义） |
| **Summary** | 最后一轮固定「总结」提示；结果打印并写入 `logs/summary-*.md` |
| **LLM 落盘** | 每轮模型调用 → `logs/yyyymmdd-hhmmss-nnnnnnnnn-ask/answer.txt` |

与 `01langchain-demo` 的差异：01 演示 Tools + Agent Loop；本例演示**跨轮上下文**与**收尾沉淀**，无工具。

## 运行

```bash
cd playground/02langchain-context
cp .env.example .env   # 或复用 ../01langchain-demo/.env
uv sync
uv run python main.py
uv run python main.py --turn "我叫 Cat" --turn "时区 UTC+8" --turn "偏好先给结论"
```

## 目录

```text
02langchain-context/
├── conversation/   # system prompt、demo turns、build_model
├── main.py
├── logs/           # ask/answer + summary-*.md
├── pyproject.toml
└── .env.example
```
