# Winnie API — LLM 透明代理

按路径前缀路由的 OpenAI / Anthropic 透明代理；厂商 Key 客户端透传。  
配置：[`config.yaml`](./config.yaml)。说明见 [docs/openai-proxy.md](../../docs/openai-proxy.md)。

## 运行

```bash
# 按需编辑 config.yaml
uv sync
uv run uvicorn winnie_api.main:app --reload --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/routes
```

## 冒烟测试

同一把 `DEEPSEEK_API_KEY` 测两种协议路由：

| 路由 | 协议 | 路径 |
|------|------|------|
| `deepseek-openai` | openai | `/deepseek-openai/v1/chat/completions` |
| `deepseek-anthropic` | anthropic | `/deepseek-anthropic/v1/messages` |

```bash
# scripts/.env 已填 DEEPSEEK_API_KEY
uv run python scripts/smoke_test.py
uv run python scripts/smoke_test.py --only openai
uv run python scripts/smoke_test.py --only anthropic
```
