# apps/api 调试经验

OpenAI 透明代理落地时的踩坑与约定。

## 上游兼容

1. **`UPSTREAM_BASE_URL` 有的带 `/v1`，有的不带**（如 DeepSeek）。启动时归一化为以 `/v1` 结尾再拼路径。
2. **thinking 模型**（DeepSeek 等）流式 delta 可能只有 `reasoning_content`、`content` 为空。answer 日志汇总时两者都要抓，否则 `text` 字段会空。
3. **`max_tokens` 过小**时常见 `finish_reason=length` 且 `content` 仍为空（token 全耗在 reasoning）。冒烟测试宜放宽或关 thinking。

## 日志

1. 目录默认 `data/logs/openai-proxy/`，gitignore，不入库。
2. 流式若客户端提前断开（如 `curl | head`），生成器可能在写 answer 前结束 → 用 `finally` 兜底落盘，避免只有 ask。
3. ask 必须保留完整 JSON body（含 `tools`），与 playground 约定一致。

## 运行

- 包布局 `src/winnie_api`，用 `uv run uvicorn winnie_api.main:app`。
- `.env` 放 `apps/api/.env`；密钥只走 `UPSTREAM_API_KEY`，不要提交。
