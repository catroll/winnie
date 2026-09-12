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
