# OpenAI 透明代理（阶段一）

> 对应 DESIGN「第一阶段：MVP（无状态对话）」与设计目标「透明 LLM 层」。

## 需求

1. **透明代理**：对外暴露 OpenAI 兼容 HTTP API（至少 `POST /v1/chat/completions`），请求体尽量原样转发到上游 OpenAI 兼容端点。
2. **无厂商锁定**：上游由环境变量配置（`UPSTREAM_BASE_URL` + `UPSTREAM_API_KEY`），可指向 OpenAI / DeepSeek / 其它兼容网关。
3. **本地日志**：每一轮客户端 ↔ 上游交互落盘为成对 `ask` / `answer` 文件，便于回看沟通记录（本阶段只做原始请求/响应留存；经验提炼属后续阶段）。
4. **流式透传**：`stream=true` 时以 SSE 原样转发上游分片，并在流结束后汇总写入 answer 日志。
5. **范围边界（本阶段不做）**：鉴权多用户、RAG、记忆提炼、LiteLLM 多模型路由（可后续替换上游或加薄路由层）。

## 契约

| 项 | 约定 |
|----|------|
| 监听 | 默认 `http://127.0.0.1:8000` |
| Chat | `POST /v1/chat/completions` |
| Models | `GET /v1/models`（透传上游；失败时返回配置的默认模型） |
| Health | `GET /healthz` |
| 鉴权 | 可选校验客户端 `Authorization: Bearer …`（`WINNIE_API_KEY`）；未设置则不校验 |
| 上游 | `UPSTREAM_BASE_URL`（须含或不含 `/v1`，代码归一化）+ `UPSTREAM_API_KEY` |

## 日志

- 目录：`data/logs/openai-proxy/`（gitignore，不入库）
- 命名：`yyyymmdd-hhmmss-nnnnnnnnn-ask.txt` / `...-answer.txt`
- ask：完整 JSON 请求体（含 `messages` / `tools` 等）
- answer：非流式为完整响应 JSON；流式为拼接后的文本 + 原始 SSE 摘要；错误写 `ERROR: …`

## 代码位置

```text
apps/api/          # FastAPI 服务
docs/openai-proxy.md
```

## 启动

```bash
cd apps/api
cp .env.example .env   # 填入 UPSTREAM_*
uv sync
uv run uvicorn winnie_api.main:app --reload --host 127.0.0.1 --port 8000
```

客户端把 `base_url` 指到本服务即可，例如：

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"ping"}]}'
```

调试经验见 [LESSONS-api.md](./LESSONS-api.md)。
