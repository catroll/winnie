# apps/api 调试经验

OpenAI / Anthropic 透明代理落地时的踩坑与约定。

## 配置

1. **不用 `.env`**：路由与选项写在 `config.yaml`；本地覆盖用 `config.local.yaml`（gitignore，可放 `proxy_api_key`）。
2. **厂商 API Key 不进配置文件**：只透传客户端 `Authorization` / `x-api-key`。
3. **用路径区分上游**：`/deepseek-openai/`、`/qwen-openai/`、`/openai/`、`/anthropic/`。
4. **协议与路径必须匹配**：openai 路由无 `/v1/messages`；anthropic 路由无 `/v1/chat/completions`。

## 上游兼容

1. **base_url 有的带 `/v1`、有的不带**：`Route.api_base()` 统一补齐。
2. **thinking 模型**流式可能只有 `reasoning_content`。
3. **Anthropic 流式**看 `content_block_delta` 的 `text_delta` / `thinking_delta`。
4. **Anthropic `max_tokens` 必填**。

## 日志

1. 按路由：`data/logs/{route}/`。
2. ask 头部必须含 **client_url**（客户请求本代理的地址）与 **upstream_url**（真实上游调用地址）。
3. 流式断开用 `finally` 兜底。
4. ask 记完整 JSON；不记密钥明文。

## 运行

- `uv run uvicorn winnie_api.main:app`
- 改 `config.yaml` 后需重启（或 `--reload`）；`GET /routes` 核对。

## API contract checks (2026-10-30)

1. **Assert against the response schema, not the ORM model.** `MeetingRead`
   deliberately omits `audio_key`; transcript segments expose
   `detected_language` and `translation_status`, not `language` or `status`.
2. **Signal:** the first end-to-end run reached `status=uploaded` and
   `status=ready`, then crashed with `KeyError: 'audio_key'`; the next run failed
   with `KeyError: 'language'`. The HTTP path was correct and the assertions were
   reading implementation names.
3. **Check:** before writing E2E assertions, read `meetings/schemas.py` or
   `app.openapi()["components"]["schemas"]` and use those keys. Keep sensitive
   fields such as `audio_key` out of the response model and, if needed, add a
   contract test that proves they stay out.
