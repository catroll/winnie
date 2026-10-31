# LLM 透明代理（阶段一）

> 对应 DESIGN「第一阶段：MVP」与「透明 LLM 层」。  
> 用 **路径前缀** 区分上游；**厂商 API Key 一律客户端透传**；运行配置用 **`config.yaml`**，不用环境变量。

## 需求

1. **路径路由**：`/{route}/v1/...` 决定打到哪家上游，例如 `/deepseek-openai/`、`/qwen-openai/`、`/openai/`、`/anthropic/`。
2. **密钥透传**：OpenAI 兼容用 `Authorization: Bearer …`；Anthropic 用 `x-api-key`（或 Bearer）。服务端不保管厂商密钥。
3. **YAML 配置**：路由、日志目录、可选大门钥写在 `apps/api/config.yaml`；本地覆盖用 `config.local.yaml`（gitignore）。
4. **协议原样转发**：`openai` → Chat Completions；`anthropic` → Messages。不做协议互转。
5. **本地日志**：按路由分目录落盘 ask/answer；流式 SSE 透传。

## 配置

见 [`apps/api/config.yaml`](../apps/api/config.yaml)。要点：

```yaml
log_root: ../../data/logs
anthropic_version: "2023-06-01"
proxy_api_key: ""   # 可选；留空不校验
routes:
  deepseek-openai:
    protocol: openai
    base_url: https://api.deepseek.com
  anthropic:
    protocol: anthropic
    base_url: https://api.anthropic.com
```

查询当前表：`GET /routes`。

## 契约

| 项 | 约定 |
|----|------|
| OpenAI Chat | `POST /{route}/v1/chat/completions`（protocol=openai） |
| Anthropic Messages | `POST /{route}/v1/messages`（protocol=anthropic） |
| Models | `GET /{route}/v1/models` |
| Health | `GET /healthz` |
| 厂商密钥 | 客户端请求头透传 |
| 代理大门钥 | `config.yaml` 的 `proxy_api_key`（可选） |

## 日志

`data/logs/{route}/`，成对 `ask` / `answer`。  
ask 头部含 `client_url`（客户请求本代理的地址）与 `upstream_url`（真实上游调用地址）。

Each call also appends one line to the usage ledger under `data/usage/` (token
classes, latency, estimated cost); it keeps counts, not prompts. See
[usage.md](./usage.md) for the fields, pricing and limits.

## 启动

```bash
cd apps/api
# 按需编辑 config.yaml；敏感覆盖写 config.local.yaml
uv sync
uv run uvicorn winnie_api.main:app --reload --host :: --port 8000
```

### 调用示例

```bash
curl -s 'http://[::1]:8000/deepseek-openai/v1/chat/completions' \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"ping"}]}'

curl -s 'http://[::1]:8000/anthropic/v1/messages' \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4-5","max_tokens":64,"messages":[{"role":"user","content":"ping"}]}'
```

客户端 `base_url` 设为 `http://[::1]:8000/{route}`。

## 冒烟测试

用 DeepSeek 同一把 Key 验证 OpenAI / Anthropic 两套代理：

```bash
cd apps/api
uv run python scripts/smoke_test.py
```

调试经验见 [LESSONS-api.md](./LESSONS-api.md)。
