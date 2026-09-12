# LLM 透明代理（阶段一）

> 对应 DESIGN「第一阶段：MVP（无状态对话）」与设计目标「透明 LLM 层」。  
> 本服务在同一端口同时提供 **OpenAI 兼容** 与 **Anthropic 原生** 透明代理，并保留本地沟通日志。

## 需求

1. **OpenAI 透明代理**：`POST /v1/chat/completions`，请求体原样转发到 OpenAI 兼容上游。
2. **Anthropic 透明代理**：`POST /v1/messages`，请求体原样转发到 Anthropic Messages API（不转换协议）。
3. **无厂商锁定**：上游分别由 `UPSTREAM_*` / `ANTHROPIC_*` 配置。
4. **本地日志**：每一轮客户端 ↔ 上游交互落盘为成对 `ask` / `answer`。
5. **流式透传**：`stream=true` 时 SSE 原样转发，结束后汇总写入 answer。
6. **范围边界（本阶段不做）**：协议互转、鉴权多用户、RAG、记忆提炼、LiteLLM 路由。

## 契约

| 项 | OpenAI 兼容 | Anthropic 原生 |
|----|-------------|----------------|
| Chat | `POST /v1/chat/completions` | `POST /v1/messages` |
| Models | `GET /v1/models`（默认） | 同路径，但带 `anthropic-version` 或仅 `x-api-key` 时走 Anthropic |
| Health | `GET /healthz` | 同左 |
| 客户端鉴权 | `Authorization: Bearer`（可选 `WINNIE_API_KEY`） | `x-api-key` 或 Bearer |
| 上游密钥 | `UPSTREAM_API_KEY` | `ANTHROPIC_API_KEY`（未配置则透传客户端 `x-api-key`） |
| 上游地址 | `UPSTREAM_BASE_URL` | `ANTHROPIC_BASE_URL`（默认 `https://api.anthropic.com`） |
| 版本头 | — | 透传 `anthropic-version`；缺省用 `ANTHROPIC_VERSION` |

## 日志

| 协议 | 目录 |
|------|------|
| OpenAI | `data/logs/openai-proxy/` |
| Anthropic | `data/logs/anthropic-proxy/` |

命名：`yyyymmdd-hhmmss-nnnnnnnnn-ask.txt` / `...-answer.txt`。

## 启动

```bash
cd apps/api
cp .env.example .env   # 填入 UPSTREAM_* / ANTHROPIC_*
uv sync
uv run uvicorn winnie_api.main:app --reload --host 127.0.0.1 --port 8000
```

### OpenAI 客户端

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"ping"}]}'
```

### Anthropic 客户端

把 SDK / CLI 的 `base_url` 指到本服务（仍请求 `/v1/messages`）：

```bash
curl -s http://127.0.0.1:8000/v1/messages \
  -H 'Content-Type: application/json' \
  -H 'anthropic-version: 2023-06-01' \
  -H 'x-api-key: $ANTHROPIC_API_KEY' \
  -d '{"model":"claude-sonnet-4-5","max_tokens":64,"messages":[{"role":"user","content":"ping"}]}'
```

调试经验见 [LESSONS-api.md](./LESSONS-api.md)。
