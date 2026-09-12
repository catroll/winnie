# apps/api 调试经验

OpenAI / Anthropic 透明代理落地时的踩坑与约定。

## 上游兼容

1. **`UPSTREAM_BASE_URL` 有的带 `/v1`，有的不带**（如 DeepSeek）。启动时归一化为以 `/v1` 结尾再拼路径。
2. **Anthropic `ANTHROPIC_BASE_URL` 官方根不含 `/v1`**；代码统一补成 `…/v1` 再拼 `/messages`。
3. **thinking 模型**（DeepSeek 等）流式 delta 可能只有 `reasoning_content`、`content` 为空。answer 日志汇总时两者都要抓。
4. **Anthropic 流式**事件是 `content_block_delta`，文本在 `delta.text`（`text_delta`）或 `delta.thinking`（`thinking_delta`），不是 OpenAI 的 `choices[].delta`。
5. **`max_tokens`**：Anthropic Messages **必填**；OpenAI 可选。代理侧对 Anthropic 缺字段直接 400。
6. **`/v1/models` 路径冲突**：用 `anthropic-version` 头（或仅有 `x-api-key`、无 Bearer）分流到 Anthropic 上游，其余走 OpenAI 兼容上游。

## 日志

1. OpenAI → `data/logs/openai-proxy/`；Anthropic → `data/logs/anthropic-proxy/`。均 gitignore。
2. 流式若客户端提前断开（如 `curl | head`），用 `finally` 兜底落盘，避免只有 ask。
3. ask 必须保留完整 JSON body（含 `tools` / Anthropic `tools`），与 playground 约定一致。

## 运行

- 包布局 `src/winnie_api`，用 `uv run uvicorn winnie_api.main:app`。
- `.env` 放 `apps/api/.env`；密钥只走 `UPSTREAM_API_KEY` / `ANTHROPIC_API_KEY`，不要提交。
- 客户端鉴权（可选 `WINNIE_API_KEY`）：OpenAI 用 Bearer，Anthropic 用 `x-api-key` 亦可。
