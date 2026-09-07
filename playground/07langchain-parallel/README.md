# 07 · langchain-parallel

教学 Demo 第 7 章：**并行执行**（企业级 AI Pipeline 雏形）。

```text
                 ┌─ 标题生成
用户文章 ────────┼─ 摘要生成
                 ├─ 情感分析
                 └─ 关键词提取
                        ↓
{
  "title": "",
  "summary": "",
  "keywords": [],
  "sentiment": ""
}
```

## 学习目标

学完本章应能：

1. 用 **RunnableParallel** 把互不依赖的子任务扇出并行。
2. 设计「一文多分析」流水线：各分支独立 Prompt + 结构化输出，最后 **merge**。
3. 理解并行的收益：总耗时接近 **最慢分支**，而非串行相加（可对照 `logs/` 里多对 ask/answer 的时间戳）。
4. （可选）用不同 **Model** 跑不同分支，体会多模型调用在 Parallel 中的接法。

## 核心知识点

| 知识点 | 要理解什么 |
|--------|------------|
| **RunnableParallel** | 同一输入同时进入多个命名分支；输出 `dict[分支名, 结果]`。 |
| **并行任务** | 分支之间无数据依赖才能真并行；有依赖应 Sequence，不要硬 Parallel。 |
| **多模型调用** | 各分支可绑定不同 ChatModel（本 Demo `--multi-model` + `OPENAI_MODEL_B`）。 |
| **企业 Pipeline** | 扇出分析 → 结构化字段 → 程序消费；与「一次让模型写整篇 JSON」相比，分支可独立演进、独立失败与限流。 |
| **与第 4/5/6 章** | 结构化合约（04）+ Parallel 积木（05）+ `\|` 组合（06）在本课收束成一条业务链。 |

## 建议重点理解

- **按职责拆分支**：标题 / 摘要 / 关键词 / 情感各管一段 Prompt，比「一个巨型 Prompt 求全」更好维护。
- **合并在边缘**：Parallel 之后用 Lambda/`Pydantic` 收成最终 JSON，作为服务 API 的返回体。
- **观察日志**：一次 `invoke` 会产生多轮 LLM 调用（四路 ≈ 四对 ask/answer）；这是正常的。
- 真生产还要补：单分支超时、失败降级、限流——本 Demo 先掌握拓扑。

## 运行

```bash
cd playground/07langchain-parallel
cp .env.example .env
uv sync
uv run python main.py
uv run python main.py --multi-model
uv run python main.py --article "本地完成知识库 MVP，团队士气高涨。"
```

## 目录

```text
07langchain-parallel/
├── pipeline/       # RunnableParallel + merge
├── main.py
├── logs/
├── pyproject.toml
└── .env.example
```
